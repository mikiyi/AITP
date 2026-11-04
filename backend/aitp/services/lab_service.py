"""AI 实验室服务：能力执行、提示词优化、智能路由。

统一契约：
- 输入 capability + variables（+ moduleId / profileId / sessionId）
- 先按功能点能力绑定解析提示词与默认变量
- 再按 AI 档案优先级依次尝试，不可用时自动降级
- 输出产出物、模型、耗时、token 用量、降级信息与数据外发边界提示
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..ai.capability_codes import (
    CAPABILITIES,
    CAP_KNOWLEDGE_QA,
    CAP_PROMPT_OPTIMIZE,
    OUTPUT_JSON,
    CapabilitySpec,
    label_of,
    spec_of,
)
from ..ai.client import chat_with_fallback, stream_with_fallback
from ..ai.resolver import resolve_candidates
from ..ai.safety import build_safety_prompt, mark_review_required, needs_human_review
from ..ai.types import ChatResult, Message, ResolvedAIConfig, StreamEvent
from ..common.constants import EGRESS_NONE, EGRESS_THIRD_PARTY
from ..errors import AppError, ErrorCode
from ..prompts.registry import get_registry
from ..repositories.module_repo import ModuleCapabilityRepository, ModuleRepository

#: 第三方模型的数据外发提示
EGRESS_WARNING = (
    "本次调用会把输入内容发送到第三方模型服务，输入中若含手机号、身份证、账号、"
    "内部 SQL 或生产日志，请先脱敏，或改用本地模型。"
)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)


@dataclass(slots=True)
class ModuleBinding:
    """功能点上的能力绑定结果。"""

    prompt_code: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    module_path: str = ""
    bound: bool = False
    enabled: bool = True


@dataclass(slots=True)
class CapabilityRun:
    capability: str
    label: str
    output: str
    raw: str
    model: str
    prompt_code: str
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)
    degrade: dict[str, Any] = field(default_factory=dict)
    egress: str = EGRESS_NONE
    egress_warning: str = ""
    module_path: str = ""
    parsed: Any = None
    refs: list[dict[str, Any]] = field(default_factory=list)
    review_required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "label": self.label,
            "output": self.output,
            "raw": self.raw,
            "model": self.model,
            "promptCode": self.prompt_code,
            "latencyMs": self.latency_ms,
            "usage": self.usage,
            "degrade": self.degrade,
            "egress": self.egress,
            "egressWarning": self.egress_warning,
            "modulePath": self.module_path,
            "parsed": self.parsed,
            "refs": self.refs,
            "reviewRequired": self.review_required,
        }


def require_capability(capability: str) -> CapabilitySpec:
    spec = spec_of(capability)
    if spec is None:
        raise AppError(ErrorCode.PARAM_ERROR, f"未知 AI 能力：{capability}")
    return spec


def resolve_binding(
    conn: sqlite3.Connection, capability: str, module_id: int | None
) -> ModuleBinding:
    """解析功能点上的能力绑定。

    带 moduleId 时，能力必须在该功能点上已启用，否则 140105；
    绑定了专属提示词就用专属的，绑定了默认参数就并入变量。
    """
    spec = require_capability(capability)
    binding = ModuleBinding(prompt_code=spec.prompt_code)
    if module_id is None:
        return binding

    module_repo = ModuleRepository(conn)
    node = module_repo.get_node(module_id)
    if not node:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "功能点不存在")

    binding.module_path = module_repo.path_text(module_id, project_name="")
    record = ModuleCapabilityRepository(conn).get_binding(module_id, capability)
    if record is None:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"功能点「{binding.module_path}」未启用「{label_of(capability)}」能力，请先在模块管理中启用。",
        )
    if not record.get("enabled"):
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"功能点「{binding.module_path}」已停用「{label_of(capability)}」能力。",
        )
    binding.bound = True
    binding.enabled = True
    if record.get("prompt_code"):
        binding.prompt_code = str(record["prompt_code"])
    binding.config = dict(record.get("config") or {})
    return binding


def merge_variables(
    spec: CapabilitySpec, variables: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """请求变量优先，功能点默认参数补缺。"""
    merged: dict[str, Any] = {}
    for name in spec.variables:
        value = variables.get(name)
        if value in (None, ""):
            value = config.get(name)
        merged[name] = value if value is not None else ""
    for key, value in config.items():
        if key not in merged:
            merged[key] = value
    return merged


def build_messages(prompt_text: str, extra_system: str = "") -> list[Message]:
    system = build_safety_prompt(extra_system)
    return [
        Message(role="system", content=system),
        Message(role="user", content=prompt_text),
    ]


def prepare_variables(
    conn: sqlite3.Connection,
    spec: CapabilitySpec,
    variables: dict[str, Any],
    binding: ModuleBinding,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """合并请求变量与功能点默认参数，并对需要检索的能力补上下文。"""
    merged = merge_variables(spec, variables, binding.config)
    refs: list[dict[str, Any]] = []
    if spec.code == CAP_KNOWLEDGE_QA:
        context, refs = retrieve_knowledge_context(conn, str(merged.get("question") or ""))
        if context:
            merged["context"] = context
    return merged, refs


def available_candidates(profile_id: int | None) -> list[ResolvedAIConfig]:
    from flask import current_app

    key_path = current_app.config.get("AITP_SECRET_KEY_PATH")
    from pathlib import Path

    candidates = resolve_candidates(
        key_path=Path(key_path) if key_path else None,
        require_key=False,
    )
    if profile_id is not None:
        pinned = [c for c in candidates if c.profile_id == profile_id]
        if pinned:
            candidates = pinned + [c for c in candidates if c.profile_id != profile_id]
    return candidates


def _egress_of(config: ResolvedAIConfig) -> tuple[str, str]:
    egress = config.egress
    if egress == EGRESS_NONE:
        return egress, ""
    return egress, EGRESS_WARNING


def _finalize_output(text: str, spec: CapabilitySpec) -> tuple[str, Any]:
    """JSON 能力解析输出；解析失败时返回原文。"""
    if spec.output_format != OUTPUT_JSON:
        return text, None
    parsed = extract_json(text)
    if parsed is None:
        return text, None
    return text, parsed


def extract_json(text: str) -> Any | None:
    """从模型输出里提取 JSON；提取不到返回 None。"""
    if not text:
        return None
    candidates: list[str] = []
    fenced = _JSON_FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    stripped = text.strip()
    candidates.append(stripped)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


@dataclass(slots=True)
class RunPlan:
    """一次能力执行的事前准备结果。

    校验、提示词渲染、候选解析都在这里完成，出错时直接抛业务异常，
    调用方据此可以在建任务之前就返回明确的错误。
    """
    capability: str
    spec: CapabilitySpec
    binding: ModuleBinding
    prompt_text: str
    messages: list[Message]
    candidates: list[ResolvedAIConfig]
    refs: list[dict[str, Any]] = field(default_factory=list)
    profile_id: int | None = None


def prepare_run(
    conn: sqlite3.Connection,
    capability: str,
    variables: dict[str, Any],
    *,
    module_id: int | None = None,
    profile_id: int | None = None,
    extra_system: str = "",
) -> RunPlan:
    """校验并准备一次执行：变量齐全、功能点已启用、有可用模型。"""
    spec = require_capability(capability)
    binding = resolve_binding(conn, capability, module_id)
    registry = get_registry()
    merged, refs = prepare_variables(conn, spec, variables, binding)
    merged = registry.fill_defaults(binding.prompt_code, merged)
    prompt_text = registry.render(binding.prompt_code, merged)

    candidates = available_candidates(profile_id)
    if not candidates:
        raise AppError(
            ErrorCode.AI_CREDENTIAL_MISSING,
            "没有可用的 AI 模型，请先在「工作台配置 → AI配置」中设置，或配置环境变量。",
        )
    return RunPlan(
        capability=capability,
        spec=spec,
        binding=binding,
        prompt_text=prompt_text,
        messages=build_messages(prompt_text, extra_system),
        candidates=candidates,
        refs=refs,
        profile_id=profile_id,
    )


def execute_run(plan: RunPlan) -> CapabilityRun:
    """执行一次准备好的能力调用。"""
    outcome = chat_with_fallback(plan.candidates, plan.messages)
    result: ChatResult = outcome.result
    text = result.content or ""
    output, parsed = _finalize_output(text, plan.spec)
    review_required = needs_human_review(output)
    # JSON 产出物追加标记会破坏结构化解析，改为只置标志，由前端提示人工复核
    if review_required and plan.spec.output_format != OUTPUT_JSON:
        output = mark_review_required(output)

    egress, warning = _egress_of(outcome.config)
    return CapabilityRun(
        capability=plan.capability,
        label=plan.spec.label,
        output=output,
        raw=text,
        model=result.model or outcome.config.model,
        prompt_code=plan.binding.prompt_code,
        latency_ms=result.latency_ms,
        usage=result.usage.as_dict(),
        degrade=outcome.degrade.as_dict(),
        egress=egress,
        egress_warning=warning,
        module_path=plan.binding.module_path,
        parsed=parsed,
        refs=plan.refs,
        review_required=review_required,
    )


def run_capability(
    conn: sqlite3.Connection,
    capability: str,
    variables: dict[str, Any],
    *,
    module_id: int | None = None,
    profile_id: int | None = None,
    extra_system: str = "",
) -> CapabilityRun:
    """同步执行一个 AI 能力。"""
    plan = prepare_run(
        conn, capability, variables,
        module_id=module_id, profile_id=profile_id, extra_system=extra_system,
    )
    return execute_run(plan)


def stream_capability(
    conn: sqlite3.Connection,
    capability: str,
    variables: dict[str, Any],
    *,
    module_id: int | None = None,
    profile_id: int | None = None,
    extra_system: str = "",
) -> tuple[Iterator[StreamEvent], RunPlan]:
    """流式执行一个 AI 能力。

    返回 (事件流, 准备结果)；事件流内部已经处理了候选降级。
    """
    plan = prepare_run(
        conn, capability, variables,
        module_id=module_id, profile_id=profile_id, extra_system=extra_system,
    )
    return stream_with_fallback(plan.candidates, plan.messages), plan


def optional_variables_of(prompt_code: str) -> tuple[str, ...]:
    """提示词里可选的变量名；用于判断这次请求缺了哪些可选信息。"""
    if not prompt_code:
        return ()
    try:
        template = get_registry().get(prompt_code)
    except Exception:  # noqa: BLE001 - 自定义提示词可能不存在，缺项判定不该阻断落库
        return ()
    return tuple(template.optional)


# ---------------- 提示词优化 ----------------

def optimize_prompt(capability: str, raw_text: str) -> dict[str, Any]:
    """把用户的粗略输入改写成结构清晰的提示词。

    失败时保留原文并给出提示，不阻断主流程。
    """
    spec = spec_of(capability) or spec_of(CAP_PROMPT_OPTIMIZE)
    assert spec is not None
    registry = get_registry()
    variables = {
        "capability": spec.label,
        "capabilityGoal": spec.goal,
        "rawText": raw_text,
    }
    prompt_text = registry.render("prompt-optimizer", variables)
    messages = build_messages(prompt_text)

    try:
        outcome = chat_with_fallback(available_candidates(None), messages)
    except Exception as exc:  # noqa: BLE001 - 优化失败不阻断主流程
        return {
            "optimized": raw_text,
            "original": raw_text,
            "diff": [],
            "model": "",
            "usage": {},
            "failed": True,
            "message": f"优化失败，已使用原文：{exc}",
        }

    optimized = (outcome.result.content or "").strip()
    if not optimized:
        return {
            "optimized": raw_text,
            "original": raw_text,
            "diff": [],
            "model": outcome.result.model,
            "usage": outcome.result.usage.as_dict(),
            "failed": True,
            "message": "优化失败，已使用原文。",
        }
    return {
        "optimized": optimized,
        "original": raw_text,
        "diff": _diff_lines(raw_text, optimized),
        "model": outcome.result.model,
        "usage": outcome.result.usage.as_dict(),
        "failed": False,
        "message": "已优化，请确认后再执行。",
    }


def _diff_lines(original: str, optimized: str) -> list[str]:
    import difflib

    return list(difflib.unified_diff(
        original.splitlines(),
        optimized.splitlines(),
        fromfile="原文",
        tofile="改写后",
        lineterm="",
    ))


# ---------------- 智能路由 ----------------

def capability_catalog() -> list[dict[str, Any]]:
    return [
        {
            "code": spec.code,
            "label": spec.label,
            "goal": spec.goal,
            "keywords": list(spec.keywords),
        }
        for spec in CAPABILITIES.values()
    ]


def score_capabilities(text: str, top_k: int) -> list[dict[str, Any]]:
    """离线兜底：按关键词命中数打分。"""
    lowered = text.lower()
    scored: list[dict[str, Any]] = []
    for spec in CAPABILITIES.values():
        hits = [kw for kw in spec.keywords if kw and kw.lower() in lowered]
        if not hits:
            continue
        score = min(0.95, 0.35 + 0.15 * len(hits))
        scored.append({
            "capability": spec.code,
            "label": spec.label,
            "score": round(score, 3),
            "reason": f"文本包含关键词：{'、'.join(hits[:3])}",
            "source": "fallback",
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_k]


def score_modules(text: str, options: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """离线兜底：按功能点名称/路径包含度打分。"""
    lowered = text.lower()
    scored: list[dict[str, Any]] = []
    for option in options:
        name = str(option.get("name") or "")
        path = str(option.get("path") or "")
        if not name:
            continue
        score = 0.0
        matched: list[str] = []
        if name and name.lower() in lowered:
            score += 0.6
            matched.append(name)
        for part in re.split(r"[/>\s]+", path):
            part = part.strip()
            if len(part) >= 2 and part.lower() in lowered:
                score += 0.2
                matched.append(part)
        if score <= 0:
            continue
        scored.append({
            "moduleId": option.get("id"),
            "path": path,
            "score": round(min(0.95, score), 3),
            "reason": f"文本命中：{'、'.join(list(dict.fromkeys(matched))[:3])}" if matched else "路径相关",
            "source": "fallback",
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_k]


def parse_route_payload(text: str) -> dict[str, Any]:
    """解析模型输出的路由 JSON。"""
    parsed = extract_json(text)
    if not isinstance(parsed, dict):
        return {}
    routes = parsed.get("routes")
    return {"routes": routes} if isinstance(routes, list) else {}


def normalize_capability_routes(raw: list[Any], top_k: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("capability") or "")
        if code not in CAPABILITIES:
            continue
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        result.append({
            "capability": code,
            "label": label_of(code),
            "score": round(max(0.0, min(1.0, score)), 3),
            "reason": str(item.get("reason") or ""),
        })
    result.sort(key=lambda item: item["score"], reverse=True)
    return result[:top_k]


def normalize_module_routes(raw: list[Any], top_k: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            module_id = int(item.get("moduleId", 0))
        except (TypeError, ValueError):
            continue
        if module_id <= 0:
            continue
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        result.append({
            "moduleId": module_id,
            "path": str(item.get("path") or ""),
            "score": round(max(0.0, min(1.0, score)), 3),
            "reason": str(item.get("reason") or ""),
        })
    result.sort(key=lambda item: item["score"], reverse=True)
    return result[:top_k]


# ---------------- 路由输入准备 ----------------

def module_options(conn: sqlite3.Connection, project_id: int | None) -> list[dict[str, Any]]:
    """项目下的全部节点（模块/子模块/功能），带完整路径，供功能点路由使用。"""
    from ..repositories.project_repo import ProjectRepository

    module_repo = ModuleRepository(conn)
    if project_id is None:
        projects = ProjectRepository(conn).list_all({"status": "active"}, None, limit=200)
        if not projects:
            return []
        project_id = int(projects[0]["id"])
    project = ProjectRepository(conn).get(project_id)
    if not project:
        return []
    project_name = str(project.get("name") or "")
    options: list[dict[str, Any]] = []
    for node in module_repo.list_all(project_id):
        options.append({
            "id": int(node["id"]),
            "name": str(node["name"]),
            "nodeType": str(node["node_type"]),
            "projectId": project_id,
            "path": module_repo.path_text(int(node["id"]), project_name=project_name),
        })
    return options


def _capability_catalog_text(catalog: list[dict[str, Any]]) -> str:
    lines = []
    for item in catalog:
        keywords = "、".join(item.get("keywords") or [])
        lines.append(f"- {item['code']}（{item['label']}）：{item['goal']}；关键词：{keywords}")
    return "\n".join(lines)


def _module_catalog_text(options: list[dict[str, Any]]) -> str:
    lines = []
    for item in options:
        lines.append(f"- id={item['id']}｜{item['path']}｜类型：{item['nodeType']}")
    return "\n".join(lines)


def route_text(
    conn: sqlite3.Connection,
    text: str,
    *,
    project_id: int | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    """智能路由：一次给出「用哪个能力」与「归到哪个功能点」。

    模型可用时走提示词路由，模型不可用或输出不可解析时按关键词打分兜底，
    保证没有 API Key 也能给出候选。
    """
    registry = get_registry()
    catalog = capability_catalog()
    source = "fallback"
    capability_routes: list[dict[str, Any]] = []
    module_routes: list[dict[str, Any]] = []

    try:
        prompt = registry.render("intent-router", {
            "rawText": text,
            "capabilityCatalog": _capability_catalog_text(catalog),
        })
        outcome = chat_with_fallback(available_candidates(None), build_messages(prompt))
        payload = parse_route_payload(outcome.result.content or "")
        capability_routes = normalize_capability_routes(payload.get("routes") or [], top_k)
        if capability_routes:
            source = "ai"
    except Exception:
        capability_routes = []
    if not capability_routes:
        capability_routes = score_capabilities(text, top_k)

    options = module_options(conn, project_id)
    if options:
        try:
            prompt = registry.render("module-router", {
                "rawText": text,
                "moduleCatalog": _module_catalog_text(options),
            })
            outcome = chat_with_fallback(available_candidates(None), build_messages(prompt))
            payload = parse_route_payload(outcome.result.content or "")
            module_routes = normalize_module_routes(payload.get("routes") or [], top_k)
            if module_routes:
                source = "ai"
        except Exception:
            module_routes = []
        if not module_routes:
            module_routes = score_modules(text, options, top_k)

    # 能力候选带上最匹配的功能点，前端点选后可直接带着归属进入
    best_module = module_routes[0] if module_routes else None
    for route in capability_routes:
        if best_module:
            route["targetModuleId"] = best_module["moduleId"]
            route["targetModulePath"] = best_module["path"]
        else:
            route["targetModuleId"] = None
            route["targetModulePath"] = ""

    return {
        "capabilityRoutes": capability_routes,
        "moduleRoutes": module_routes,
        "source": source,
        "text": text,
    }


# ---------------- 知识问答上下文 ----------------

def retrieve_knowledge_context(
    conn: sqlite3.Connection, question: str, limit: int = 5
) -> tuple[str, list[dict[str, Any]]]:
    """从正式知识库检索上下文，返回 (带 [K1] 标记的正文, 引用清单)。"""
    from ..repositories.knowledge_repo import KnowledgeRepository

    if not question.strip():
        return "", []
    try:
        result = KnowledgeRepository(conn).search(question, page_num=1, page_size=limit)
    except Exception:
        return "", []
    rows = result.get("list") or []
    if not rows:
        return "", []

    blocks: list[str] = []
    refs: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        marker = f"K{index}"
        title = str(row.get("title") or "")
        content = str(row.get("content") or "")
        blocks.append(f"[{marker}] {title}\n{content}")
        refs.append({
            "marker": marker,
            "articleId": row.get("id"),
            "title": title,
            "category": row.get("category"),
        })
    return "\n\n".join(blocks), refs
