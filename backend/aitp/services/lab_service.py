"""AI 实验室服务：能力执行、提示词优化、智能路由。

统一契约：
- 输入 capability + variables（+ moduleId / profileId / sessionId）
- 先按功能点能力绑定解析提示词与默认变量
- 再按 AI 档案优先级依次尝试，不可用时自动降级
- 输出产出物、模型、耗时、token 用量、降级信息与数据外发边界提示
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

from ..ai import cost as ai_cost
from ..ai.capability_codes import (
    CAPABILITIES,
    CAP_PROMPT_OPTIMIZE,
    OUTPUT_JSON,
    CapabilitySpec,
    label_of,
    spec_of,
)
from ..ai.client import ChatOutcome, chat_with_fallback, stream_with_fallback
from ..ai.masking import MaskInfo, cloud_mask_enabled
from ..ai.output_schema import (
    MAX_SCHEMA_RETRIES,
    SchemaReport,
    has_schema,
    retry_feedback,
    schema_of,
    validate_output,
)
from ..ai.resolver import resolve_candidates
from ..ai.safety import build_safety_prompt, mark_review_required, needs_human_review
from ..ai.types import ChatResult, Message, ResolvedAIConfig, StreamEvent
from ..common.constants import DEGRADE_EMPTY_OUTPUT, EGRESS_NONE, EGRESS_THIRD_PARTY
from ..errors import AppError, ErrorCode
from ..db.connection import get_db
from ..prompts.registry import get_registry
from ..repositories.module_repo import ModuleCapabilityRepository, ModuleRepository
from . import context_assembler, dedup_service, prompt_usage

#: 第三方模型的数据外发提示
EGRESS_WARNING = (
    "本次调用会把输入内容发送到第三方模型服务，输入中若含手机号、身份证、账号、"
    "内部 SQL 或生产日志，请先脱敏，或改用本地模型。"
)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)

logger = logging.getLogger(__name__)


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
    #: 本次调用的外发脱敏结果（类型与数量），落库与前端提示都用它
    mask: dict[str, Any] = field(default_factory=lambda: MaskInfo().as_dict())
    #: GAP-28 输出 schema 校验结论：通过/拦下多少条、原因、重试次数
    schema: dict[str, Any] = field(default_factory=dict)
    #: GAP-27 本次上下文装配结论：档位、条目数、是否裁剪
    context: dict[str, Any] = field(default_factory=dict)
    #: GAP-29 本次去重判定：模式、比较域、疑似重复清单、摘走的条目
    dedup: dict[str, Any] = field(default_factory=dict)
    #: GAP-30 本次调用的成本（单价口径与金额；没配单价时只有 token）
    cost: dict[str, Any] = field(default_factory=dict)

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
            "mask": self.mask,
            "schema": self.schema,
            "context": self.context,
            "dedup": self.dedup,
            "cost": self.cost,
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


def build_messages(
    prompt_text: str,
    extra_system: str = "",
    images: list[str] | None = None,
) -> list[Message]:
    system = build_safety_prompt(extra_system)
    return [
        Message(role="system", content=system),
        Message(role="user", content=prompt_text, images=list(images or [])),
    ]


def prepare_variables(
    spec: CapabilitySpec,
    variables: dict[str, Any],
    binding: ModuleBinding,
) -> dict[str, Any]:
    """合并请求变量与功能点默认参数。"""
    merged = merge_variables(spec, variables, binding.config)
    # 归属模块类能力（缺陷归类）需要候选模块；绑定了功能点时自动带入路径
    if (
        "modulePath" in spec.variables
        and not str(merged.get("modulePath") or "").strip()
        and binding.module_path
    ):
        merged["modulePath"] = binding.module_path
    return merged


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
    """JSON 能力解析输出；解析失败时返回原文。

    保留给非校验链路（如路由/问答）复用；能力执行走 `_apply_schema`。
    """
    if spec.output_format != OUTPUT_JSON:
        return text, None
    parsed = extract_json(text)
    if parsed is None:
        return text, None
    return text, parsed


def _apply_schema(spec: CapabilitySpec, text: str) -> tuple[str, Any, SchemaReport]:
    """解析 → 校验 → 规范化。

    校验通过时返回规范化后的 JSON（多余字段已裁、默认值已补、坏条目已摘），
    前端拿到的 output 与 parsed 因此与「通过校验的条数」完全一致。
    校验不通过时**保留原文并给出原因**，不再静默回退。
    """
    if spec.output_format != OUTPUT_JSON:
        return text, None, _markdown_report(spec)
    parsed = extract_json(text)
    report = validate_output(spec.code, parsed)
    if not report.ok:
        return text, parsed, report
    normalized = report.value
    try:
        output = json.dumps(normalized, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - 规范化产物一定是 JSON-safe
        return text, parsed, report
    return output, normalized, report


def _markdown_report(spec: CapabilitySpec) -> SchemaReport:
    """markdown 能力不做结构校验，但结论仍要回给界面，不能让前端猜。"""
    return SchemaReport(
        capability=spec.code, label="", root="markdown", parsed=True, ok=True,
        message="该能力产出为 Markdown，无字段校验",
    )


@dataclass(slots=True)
class OutputFinalization:
    """一次输出收尾的结果：产出、结构化结果、校验结论与重试信息。"""

    output: str
    parsed: Any
    report: SchemaReport
    raw: str = ""
    retries: int = 0
    retry_failed: str = ""
    #: 修正重试那次调用的用量与模型（没有发生修正时为空）
    retry_usage: dict[str, Any] = field(default_factory=dict)
    retry_model: str = ""
    #: GAP-29 去重判定结论（比较域、疑似/摘走清单）；没参与去重的能力为空
    dedup: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RepairResult:
    """一次修正调用的返回：正文、该次的用量与模型名。"""

    text: str
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""


#: 修正调用的签名：(上一次输出, 校验结论) -> 模型的新输出
RepairCall = Callable[[str, SchemaReport], RepairResult]


@dataclass(slots=True)
class DedupScope:
    """GAP-29 的比较域：和谁比、比到什么程度就摘。

    放在 plan 上而不是散在参数里，同步链路与流式链路才能共用一套口径。
    """

    conn: sqlite3.Connection
    mode: str = dedup_service.MODE_MARK
    project_id: int | None = None
    module_id: int | None = None
    requirement_id: int | None = None


def _score(report: SchemaReport) -> int:
    """比较两次输出哪个更可用：先看到手的条数，再看能不能解析。"""
    return report.passed * 10 + (1 if report.parsed else 0)


def _apply_dedup(finalization: OutputFinalization, scope: DedupScope, capability: str) -> None:
    """就地把去重结论写进收尾结果；`increment` 模式下会同步改写产出。

    比对本身是纯函数、可复算；召回基线要读库，所以读库失败只留痕，
    绝不因为「比对没做成」把已经生成的产出判死。
    """
    items = finalization.parsed
    if not isinstance(items, list):
        finalization.dedup = {
            "capability": capability,
            "mode": scope.mode,
            "modeLabel": dedup_service.MODE_LABELS.get(scope.mode, scope.mode),
            "applicable": False,
            "compared": 0,
            "suspectCount": 0,
            "droppedCount": 0,
            "notes": ["产出不是可逐条比对的数组，本次不做去重"],
        }
        return
    try:
        kept, report = dedup_service.dedup_output(
            scope.conn,
            capability,
            items,
            project_id=scope.project_id,
            module_id=scope.module_id,
            requirement_id=scope.requirement_id,
            mode=scope.mode,
        )
    except (sqlite3.Error, OSError, ValueError) as exc:
        logger.warning("生成去重比对失败（capability=%s）: %s", capability, exc)
        finalization.dedup = {
            "capability": capability,
            "mode": scope.mode,
            "modeLabel": dedup_service.MODE_LABELS.get(scope.mode, scope.mode),
            "applicable": True,
            "compared": len(items),
            "suspectCount": 0,
            "droppedCount": 0,
            "error": f"去重比对失败（{type(exc).__name__}），本次未做判定",
            "notes": ["去重比对读库失败，产出按原样返回"],
        }
        return
    finalization.dedup = report.as_dict()
    finalization.dedup["applicable"] = True
    if len(kept) != len(items):
        # 摘走的条目回写产出：界面勾选的就是剩下的这些，条数与 dedup 结论必然一致
        finalization.parsed = kept
        try:
            finalization.output = json.dumps(kept, ensure_ascii=False)
        except (TypeError, ValueError):  # pragma: no cover - 规范化产物一定可序列化
            finalization.parsed = items
            finalization.dedup["notes"].append("摘除后的产出无法序列化，已回退为原始条目")


def finalize_output(
    spec: CapabilitySpec,
    text: str,
    *,
    repair: RepairCall | None = None,
    dedup: DedupScope | None = None,
) -> OutputFinalization:
    """解析 → 校验 → 规范化 → 去重；不合格就地带一次修正重试。

    - 修正重试上限写死 `MAX_SCHEMA_RETRIES = 1`（红线：不无限烧 token），
      重试请求会把具体校验错误回传给模型；
    - 修正调用本身失败（网络 / 额度 / 降级）不阻断主链路，只把原因记进结论；
    - 最终仍不合格时，产出保留原文 + 可读原因，**不静默回退**；
    - 去重在校验之后：只判定规范化留下的条目，摘走的是「确定重复」，
      疑似重复在 `mark` 模式下永不丢弃（红线：判定可复算、标记可撤销）。
    """
    finalization = _finalize_schema(spec, text, repair=repair)
    if dedup is not None:
        _apply_dedup(finalization, dedup, spec.code)
    return finalization


def _finalize_schema(
    spec: CapabilitySpec,
    text: str,
    *,
    repair: RepairCall | None = None,
) -> OutputFinalization:
    """校验与规范化那一段；不含去重，便于单独复算。"""
    output, parsed, report = _apply_schema(spec, text)
    finalization = OutputFinalization(output=output, parsed=parsed, report=report, raw=text)
    if report.ok or spec.output_format != OUTPUT_JSON or not has_schema(spec.code):
        return finalization
    if repair is None or MAX_SCHEMA_RETRIES <= 0:
        return finalization

    try:
        candidate = repair(text, report)
    except Exception as exc:  # noqa: BLE001 - 修正失败保留上一次结论，不能带崩主链路
        logger.warning("输出校验修正调用失败: %s", exc)
        finalization.retries = 1
        finalization.retry_failed = f"{type(exc).__name__}: {exc}"
        report.retried = 1
        report.message = f"{report.message}；自动修正调用失败（{finalization.retry_failed}）"
        return finalization

    finalization.retries = 1
    finalization.retry_usage = dict(candidate.usage)
    finalization.retry_model = candidate.model
    report.retried = 1
    if not candidate.text.strip():
        finalization.retry_failed = "修正调用没有返回正文"
        report.message = f"{report.message}；自动修正调用没有返回正文"
        return finalization

    new_output, new_parsed, new_report = _apply_schema(spec, candidate.text)
    new_report.retried = 1
    if _score(new_report) > _score(report):
        finalization.output = new_output
        finalization.parsed = new_parsed
        finalization.raw = candidate.text
        finalization.report = new_report
        if not new_report.ok:
            new_report.message = f"{new_report.message}（已自动修正重试 1 次）"
        return finalization

    report.message = f"{report.message}；已自动修正重试 1 次仍不合关键字段要求"
    finalization.report = report
    return finalization


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
    #: 已去水印的图片（data URL），随消息一起发给多模态模型
    images: list[str] = field(default_factory=list)
    #: 云端候选是否脱敏；来自系统配置 ai.mask.enabled，本地候选永远不脱敏
    mask_enabled: bool = True
    #: GAP-27 本次装配的业务上下文（档位、条目、裁剪情况都在里面）
    context: context_assembler.ContextPlan | None = None
    #: GAP-29 本次去重模式：mark 只标记 / increment 摘走确定重复
    dedup_mode: str = dedup_service.MODE_MARK
    #: GAP-29 比较域（项目 / 功能点 / 需求）；建 plan 时算好，链路两端共用
    dedup_project_id: int | None = None
    dedup_module_id: int | None = None
    dedup_requirement_id: int | None = None
    #: 增量模式注入的基线说明（同步/流式都靠它，界面上能看清「模型被告知了什么」）
    dedup_baseline: str = ""
    dedup_baseline_count: int = 0


def _context_project_id(
    conn: sqlite3.Connection, module_id: int | None, project_id: int | None
) -> int | None:
    """装配上下文要先确定项目：显式 projectId 优先，其次取功能点所属项目。"""
    if project_id is not None:
        return int(project_id)
    if module_id is None:
        return None
    try:
        node = ModuleRepository(conn).get_node(int(module_id))
    except sqlite3.Error:  # noqa: BLE001 - 取不到项目就是不装配，不该挡住能力执行
        return None
    if not node:
        return None
    raw = int(node.get("project_id") or 0)
    return raw if raw > 0 else None


def _assemble_context(
    conn: sqlite3.Connection,
    spec: CapabilitySpec,
    merged: dict[str, Any],
    binding: ModuleBinding,
    *,
    module_id: int | None,
    project_id: int | None,
    context_tier: str | None,
) -> context_assembler.ContextPlan | None:
    """装配业务上下文并写入提示词变量；任何意外都退化成「没装到」，不能挡住能力执行。"""
    if not spec.context_kinds:
        return None
    placeholders = {
        context_assembler.VARIABLE_OF_KIND[kind]
        for kind in spec.context_kinds
        if kind in context_assembler.VARIABLE_OF_KIND
    }
    try:
        plan = context_assembler.assemble(
            conn,
            capability=spec.code,
            variables=merged,
            project_id=_context_project_id(conn, module_id, project_id),
            module_id=module_id,
            spec=spec,
            requested_tier=context_tier,
            binding_config=binding.config,
        )
    except Exception as exc:  # noqa: BLE001 - 装配是增强项，失败只留痕不带崩主链路
        logger.warning("上下文装配失败（capability=%s）: %s", spec.code, exc)
        plan = context_assembler.ContextPlan(
            capability=spec.code,
            tier=context_assembler.normalize_tier(context_tier) or context_assembler.DEFAULT_TIER,
            project_id=project_id,
            module_id=module_id,
            notes=[f"上下文装配失败（{type(exc).__name__}），本次未注入项目上下文"],
        )
        merged.update({name: "（项目上下文装配失败，本次未注入）" for name in placeholders})
        return plan
    values = plan.variables()
    merged.update({name: values[name] for name in placeholders if name in values})
    return plan


def _prepare_dedup(
    conn: sqlite3.Connection,
    spec: CapabilitySpec,
    merged: dict[str, Any],
    assembly: context_assembler.ContextPlan | None,
    binding: ModuleBinding,
    *,
    module_id: int | None,
    project_id: int | None,
    requested_mode: str | None,
) -> tuple[str, int, int | None, int | None, str]:
    """解析去重模式；增量模式还要把「已有基线」写成给模型的指令。

    返回（模式, 基线条数, 比较项目, 比较需求, 基线指令）。基线只在 `increment`
    下生成——`mark` 模式不改模型的输入，只改我们对产出的解读。
    """
    mode = dedup_service.resolve_mode(
        conn, spec.code, requested=requested_mode, binding_config=binding.config,
    )
    scope_project = (
        assembly.project_id if assembly is not None and assembly.project_id
        else _context_project_id(conn, module_id, project_id)
    )
    if mode != dedup_service.MODE_INCREMENT or spec.code not in dedup_service.DEDUP_CAPABILITIES:
        return mode, 0, scope_project, None, ""

    keywords = list(assembly.keywords) if assembly is not None else []
    if not keywords:
        keywords = context_assembler.keywords_of(
            context_assembler.query_text_of(spec, merged)
        )
    requirement_id: int | None = None
    if scope_project is not None and keywords:
        try:
            requirement_id = context_assembler.related_requirement_id(conn, scope_project, keywords)
        except sqlite3.Error as exc:  # noqa: BLE001 - 对不上需求就退化成同项目基线
            logger.warning("增量基线的需求对齐失败: %s", exc)
    if scope_project is None:
        return mode, 0, None, None, ""
    try:
        existing, _scope = dedup_service.existing_cases(
            conn,
            project_id=scope_project,
            module_id=module_id,
            requirement_id=requirement_id if spec.code != "regression_generation" else None,
        )
    except sqlite3.Error as exc:  # noqa: BLE001 - 没有基线就按普通生成走
        logger.warning("增量基线召回失败: %s", exc)
        return mode, 0, scope_project, requirement_id, ""
    directive = dedup_service.baseline_directive(spec.code, existing)
    return mode, len(existing), scope_project, requirement_id, directive


def prepare_run(
    conn: sqlite3.Connection,
    capability: str,
    variables: dict[str, Any],
    *,
    module_id: int | None = None,
    profile_id: int | None = None,
    extra_system: str = "",
    images: list[str] | None = None,
    context_tier: str | None = None,
    project_id: int | None = None,
    dedup_mode: str | None = None,
) -> RunPlan:
    """校验并准备一次执行：变量齐全、功能点已启用、上下文已装配、有可用模型。"""
    spec = require_capability(capability)
    binding = resolve_binding(conn, capability, module_id)
    registry = get_registry()
    merged = prepare_variables(spec, variables, binding)
    assembly = _assemble_context(
        conn, spec, merged, binding,
        module_id=module_id, project_id=project_id, context_tier=context_tier,
    )
    resolved_mode, baseline_count, scope_project, scope_requirement, baseline = _prepare_dedup(
        conn, spec, merged, assembly, binding,
        module_id=module_id, project_id=project_id, requested_mode=dedup_mode,
    )
    if baseline:
        # 基线走系统提示，不进用户提示词模板：模板变量表是被门禁锁死的，
        # 而且「已有用例清单」属于系统给模型的约束，不是用户填的业务信息。
        extra_system = "\n\n".join(part for part in (extra_system, baseline) if part)
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
        messages=build_messages(prompt_text, extra_system, images),
        candidates=candidates,
        profile_id=profile_id,
        images=list(images or []),
        mask_enabled=cloud_mask_enabled(conn),
        context=assembly,
        dedup_mode=resolved_mode,
        dedup_project_id=scope_project,
        dedup_module_id=module_id,
        dedup_requirement_id=scope_requirement,
        dedup_baseline=baseline,
        dedup_baseline_count=baseline_count,
    )


def _dedup_scope(
    plan: RunPlan, conn: sqlite3.Connection | None
) -> DedupScope | None:
    """只有纳入去重口径的能力才建比较域；其余能力连判定都不做，不留空壳。"""
    if conn is None or plan.capability not in dedup_service.DEDUP_CAPABILITIES:
        return None
    return DedupScope(
        conn=conn,
        mode=plan.dedup_mode,
        project_id=plan.dedup_project_id,
        module_id=plan.dedup_module_id,
        requirement_id=plan.dedup_requirement_id,
    )


def execute_run(
    plan: RunPlan, *, conn: sqlite3.Connection | None = None
) -> CapabilityRun:
    """执行一次准备好的能力调用；JSON 产出走「解析 → 校验 → 规范化 → 去重」。"""
    outcomes: list[ChatOutcome] = [
        chat_with_fallback(plan.candidates, plan.messages, mask_enabled=plan.mask_enabled)
    ]

    def repair(text: str, report: SchemaReport) -> RepairResult:
        """把校验错误回传给模型再要一次输出（协议不变，只是多一轮对话）。"""
        messages = list(plan.messages) + [
            Message(role="assistant", content=text),
            Message(role="user", content=retry_feedback(report)),
        ]
        outcome = chat_with_fallback(plan.candidates, messages, mask_enabled=plan.mask_enabled)
        outcomes.append(outcome)
        return RepairResult(
            text=outcome.result.content or "",
            usage=_merge_usages([outcome.result.usage]),
            model=outcome.result.model,
        )

    outcome = outcomes[0]
    text = outcome.result.content or ""
    finalization = finalize_output(
        plan.spec, text,
        repair=repair if plan.spec.output_format == OUTPUT_JSON else None,
        dedup=_dedup_scope(plan, conn),
    )
    output = finalization.output
    parsed = finalization.parsed
    review_required = needs_human_review(output)
    # JSON 产出物追加标记会破坏结构化解析，改为只置标志，由前端提示人工复核
    if review_required and plan.spec.output_format != OUTPUT_JSON:
        output = mark_review_required(output)

    chosen = _outcome_of_text(outcomes, finalization.raw)
    egress, warning = _egress_of(outcome.config)
    if not text.strip() and not outcome.degrade.degraded:
        # 推理模型常把输出额度全花在思考上，正文为空时不能只留一个空白产出框
        outcome.degrade.degraded = True
        outcome.degrade.reason = DEGRADE_EMPTY_OUTPUT
        outcome.degrade.to_config = chosen.result.model or outcome.config.model
        outcome.degrade.message = (
            "模型没有返回正文（推理内容可能已占满输出额度）。"
            "请在「AI 配置」里调大该档案的「单次最大 Token」后重试。"
        )
    schema_payload = schema_payload_of(finalization)
    dedup_payload = dedup_payload_of(finalization, plan)
    merged_usage = _merge_usages([item.result.usage for item in outcomes])
    return CapabilityRun(
        capability=plan.capability,
        label=plan.spec.label,
        output=output,
        raw=finalization.raw or text,
        model=chosen.result.model or outcome.config.model,
        prompt_code=plan.binding.prompt_code,
        latency_ms=sum(item.result.latency_ms for item in outcomes),
        usage=merged_usage,
        degrade=outcome.degrade.as_dict(),
        egress=egress,
        egress_warning=warning,
        module_path=plan.binding.module_path,
        parsed=parsed,
        refs=plan.refs,
        review_required=review_required,
        mask=outcome.mask.as_dict(),
        schema=schema_payload,
        context=plan.context.as_dict() if plan.context is not None else {},
        dedup=dedup_payload,
        cost=build_cost_payload(
            plan, merged_usage, chosen.config,
            chosen.result.model or outcome.config.model,
            conn=conn,
        ),
    )


def _outcome_of_text(outcomes: list[ChatOutcome], content: str) -> ChatOutcome:
    """找到最终采用那次输出对应的调用结果（模型名以它为准）。"""
    for item in reversed(outcomes):
        if (item.result.content or "") == content:
            return item
    return outcomes[-1]


def _merge_usages(usages: list[Any]) -> dict[str, Any]:
    """把修正重试的 token 一并算进本次用量，成本口径不能只算第一次。"""
    total = {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0, "estimated": False}
    for usage in usages:
        total["promptTokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
        total["completionTokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
        total["totalTokens"] += int(getattr(usage, "total_tokens", 0) or 0)
        total["estimated"] = total["estimated"] or bool(getattr(usage, "estimated", False))
    return total


def schema_payload_of(finalization: OutputFinalization) -> dict[str, Any]:
    """GAP-28 结论的统一口径：校验报告 + 修正重试的痕迹（次数/用量/模型/失败原因）。

    同步与流式两条链路都走它，历史面板与响应体才不会两套字段。
    """
    payload = finalization.report.as_dict()
    if finalization.retry_failed:
        payload["retryFailed"] = finalization.retry_failed
    if finalization.retry_usage:
        payload["retryUsage"] = finalization.retry_usage
    if finalization.retry_model:
        payload["retryModel"] = finalization.retry_model
    return payload


def dedup_payload_of(finalization: OutputFinalization, plan: RunPlan) -> dict[str, Any]:
    """GAP-29 结论的统一口径：模式中文名 + 基线到底有没有告诉模型。"""
    payload = dict(finalization.dedup)
    if not payload:
        return payload
    payload.setdefault("mode", plan.dedup_mode)
    payload["modeLabel"] = dedup_service.MODE_LABELS.get(
        str(payload.get("mode") or plan.dedup_mode), plan.dedup_mode,
    )
    payload["baselineCount"] = plan.dedup_baseline_count
    payload["baselineInjected"] = bool(plan.dedup_baseline)
    return payload


def build_cost_payload(
    plan: RunPlan,
    usage: dict[str, Any],
    config: ResolvedAIConfig,
    model: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """GAP-30 成本结论：token × 档案单价（微元），再叠上软预算与单次 token 上限的提示。

    两条口径必须守住：
    - 未配价时 `costMicros` 是 None（落库为 NULL），不是 0——0 会被日/月汇总当成免费；
    - 预算与 token 上限都是**软提示**，不拦截、不降级（工程清单第 7 节决策 3），
      所以这里读不到配置就只少一份提示，绝不能反过来让已经拿到的产出失败。
    """
    price = ai_cost.TokenPrice(
        input_micros=int(config.price_input_micros or 0),
        output_micros=int(config.price_output_micros or 0),
        currency=config.price_currency or ai_cost.DEFAULT_CURRENCY,
    )
    payload = ai_cost.cost_report(
        usage, price,
        capability=plan.capability,
        profile_id=config.profile_id,
        profile_name=str(config.profile_name or ""),
        model=model,
    )
    if conn is None:
        return payload
    try:
        status = ai_cost.budget_status(
            conn,
            project_id=plan.dedup_project_id,
            capability=plan.capability,
            total_tokens=int(usage.get("totalTokens") or 0),
        )
    except sqlite3.Error:  # noqa: BLE001 - 预算读不到只少一份提示，不影响产出
        return payload
    payload["budget"] = {
        "currency": status["currency"],
        "today": status["today"],
        "month": status["month"],
        "budget": status["budget"],
        "callTokenLimit": status["callTokenLimit"],
        "callTokenHint": status["callTokenHint"],
        "warnings": status["warnings"],
        "blocking": False,
    }
    return payload


def run_capability(
    conn: sqlite3.Connection,
    capability: str,
    variables: dict[str, Any],
    *,
    module_id: int | None = None,
    profile_id: int | None = None,
    extra_system: str = "",
    images: list[str] | None = None,
    context_tier: str | None = None,
    project_id: int | None = None,
    dedup_mode: str | None = None,
) -> CapabilityRun:
    """同步执行一个 AI 能力。"""
    plan = prepare_run(
        conn, capability, variables,
        module_id=module_id, profile_id=profile_id, extra_system=extra_system,
        images=images, context_tier=context_tier, project_id=project_id,
        dedup_mode=dedup_mode,
    )
    return execute_run(plan, conn=conn)


def stream_capability(
    conn: sqlite3.Connection,
    capability: str,
    variables: dict[str, Any],
    *,
    module_id: int | None = None,
    profile_id: int | None = None,
    extra_system: str = "",
    images: list[str] | None = None,
    context_tier: str | None = None,
    project_id: int | None = None,
    dedup_mode: str | None = None,
) -> tuple[Iterator[StreamEvent], RunPlan]:
    """流式执行一个 AI 能力。

    返回 (事件流, 准备结果)；事件流内部已经处理了候选降级。
    """
    plan = prepare_run(
        conn, capability, variables,
        module_id=module_id, profile_id=profile_id, extra_system=extra_system,
        images=images, context_tier=context_tier, project_id=project_id,
        dedup_mode=dedup_mode,
    )
    return stream_with_fallback(
        plan.candidates, plan.messages, mask_enabled=plan.mask_enabled
    ), plan


def finalize_stream_output(
    plan: RunPlan, text: str, *, conn: sqlite3.Connection | None = None
) -> OutputFinalization:
    """流式产出收尾：与同步链路同一套「解析 → 校验 → 规范化 → 去重」。

    修正重试走非流式补一次调用；任何失败都只体现在结论里，不影响已经吐出去的帧。
    `conn` 由调用方给一条**自己的**连接（流式线程不能用请求连接），
    没给就跳过比对——去重是增强项，不该让产出出不来。
    """

    def repair(previous: str, report: SchemaReport) -> RepairResult:
        messages = list(plan.messages) + [
            Message(role="assistant", content=previous),
            Message(role="user", content=retry_feedback(report)),
        ]
        outcome = chat_with_fallback(plan.candidates, messages, mask_enabled=plan.mask_enabled)
        return RepairResult(
            text=outcome.result.content or "",
            usage=_merge_usages([outcome.result.usage]),
            model=outcome.result.model,
        )

    return finalize_output(
        plan.spec, text,
        repair=repair if plan.spec.output_format == OUTPUT_JSON else None,
        dedup=_dedup_scope(plan, conn),
    )


#: 属于系统侧的系统变量，不进「信息缺项」：没选模板不代表信息不足；
#: 上下文占位由装配器注入，更不是用户没填（GAP-27）
_SILENT_OPTIONAL = frozenset({"outputTemplate"}) | frozenset(
    context_assembler.VARIABLE_OF_KIND.values()
)


def optional_variables_of(prompt_code: str) -> tuple[str, ...]:
    """提示词里可选的变量名；用于判断这次请求缺了哪些可选信息。"""
    if not prompt_code:
        return ()
    try:
        template = get_registry().get(prompt_code)
    except Exception:  # noqa: BLE001 - 自定义提示词可能不存在，缺项判定不该阻断落库
        return ()
    return tuple(name for name in template.optional if name not in _SILENT_OPTIONAL)


# ---------------- 提示词优化 ----------------

def optimize_prompt(
    capability: str, raw_text: str, *, conn: sqlite3.Connection | None = None
) -> dict[str, Any]:
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
        outcome = chat_with_fallback(
            available_candidates(None), messages, mask_enabled=cloud_mask_enabled(conn)
        )
    except Exception as exc:  # noqa: BLE001 - 优化失败不阻断主流程
        return {
            "optimized": raw_text,
            "original": raw_text,
            "diff": [],
            "model": "",
            "usage": {},
            "failed": True,
            "message": f"优化失败，已使用原文：{exc}",
            "mask": MaskInfo(enabled=cloud_mask_enabled(conn)).as_dict(),
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
            "mask": outcome.mask.as_dict(),
        }
    return {
        "optimized": optimized,
        "original": raw_text,
        "diff": _diff_lines(raw_text, optimized),
        "model": outcome.result.model,
        "usage": outcome.result.usage.as_dict(),
        "failed": False,
        "message": "已优化，请确认后再执行。",
        "mask": outcome.mask.as_dict(),
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


# ---------------- 能力卡片 ----------------

def _context_card_of(spec: CapabilitySpec) -> dict[str, Any]:
    """卡片上的上下文声明：种类中文名、默认档位与该档硬上限（GAP-27 可见性）。"""
    tier = context_assembler.resolve_tier(None, spec.code)
    return {
        "kinds": [
            {
                "kind": kind,
                "label": context_assembler.KIND_LABELS.get(kind, kind),
                "variable": context_assembler.VARIABLE_OF_KIND.get(kind, ""),
            }
            for kind in spec.context_kinds
        ],
        "tiers": [
            {"value": item, "label": context_assembler.TIER_LABELS[item]}
            for item in context_assembler.CONTEXT_TIERS
        ],
        "defaultTier": tier,
        "defaultTierLabel": context_assembler.TIER_LABELS.get(tier, tier),
        "limits": dict(context_assembler.TIER_LIMITS.get(tier, {})),
    }


def _schema_card_of(spec: CapabilitySpec) -> dict[str, Any]:
    """卡片上的校验能力声明：这个能力的产出会不会做字段校验、校验什么（GAP-28 可见性）。"""
    schema = schema_of(spec.code)
    return {
        "validated": schema is not None,
        "label": schema.label if schema else "",
        "root": schema.root if schema else "",
        "unit": schema.unit if schema else "",
        "retryLimit": MAX_SCHEMA_RETRIES,
    }


def capability_card(spec: CapabilitySpec) -> dict[str, Any]:
    """能力卡片 = 能力目录 + 提示词库元数据。

    标题、说明、必填/可选变量一律以 backend/prompts 下的模板为准：
    提示词文件改了，卡片内容跟着变，不用两处维护。
    """
    data = spec.as_dict()
    data["context"] = _context_card_of(spec)
    data["schema"] = _schema_card_of(spec)
    template = None
    if spec.prompt_code:
        try:
            template = get_registry().get(spec.prompt_code)
        except Exception:  # noqa: BLE001 - 自定义提示词可能不存在或不在应用上下文里，不该让目录接口整个失败
            template = None
    if template is None:
        data.update({
            "promptTitle": spec.label,
            "promptDescription": spec.goal,
            "requiredVariables": list(spec.variables),
            "optionalVariables": [],
        })
        return data
    optional = list(template.optional)
    required = [name for name in template.variables if name not in optional]
    data.update({
        "promptTitle": template.title or spec.label,
        "promptDescription": template.description or spec.goal,
        "requiredVariables": required,
        "optionalVariables": optional,
    })
    return data


def capability_cards(codes: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """能力卡片列表；不传 codes 时返回全部能力。每张卡片带使用次数。"""
    from ..repositories.ai_record_repo import AIRecordRepository

    if codes is None:
        codes = tuple(CAPABILITIES.keys())
    use_counts: dict[str, int] = {}
    try:
        use_counts = AIRecordRepository(get_db()).count_by_capability()
    except Exception:  # noqa: BLE001 - 统计失败不该让能力目录整个失败
        use_counts = {}
    result = []
    for code in codes:
        card = capability_card(CAPABILITIES[code])
        card["useCount"] = use_counts.get(code, 0)
        result.append(card)
    return result


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


#: 引号包起来的内容通常是「被处理的对象」，不是用户想做的事，
#: 比如「帮我对…『从需求拆解测试点…』做提示词优化」——引号里的词不该抢走意图。
_QUOTED_RE = re.compile(r"“[^”]*”|\"[^\"]*\"|「[^」]*」|『[^』]*』|`[^`]*`|'[^']*'")


def _strip_quoted(text: str) -> str:
    return _QUOTED_RE.sub(" ", text)


def score_capabilities(text: str, top_k: int) -> list[dict[str, Any]]:
    """按「能力名 > 正文关键词 > 引号内关键词」的顺序给能力打分。

    - 正文里直接出现能力名（如「提示词优化」）视为强命中，给 0.9；
    - 正文命中关键词每条 +0.15；只出现在引号里的关键词权重打四折；
    - 只提到能力名但整体在引号里（在描述别的产出物）只给 0.6。
    """
    lowered = text.lower()
    unquoted_text = _strip_quoted(text)
    unquoted = unquoted_text.lower()
    # 冒号前是「要做什么」，冒号后往往是「处理对象」，对象命中的关键词只算参考
    separators = ["：", ":", chr(10)]
    instruction = unquoted_text
    for separator in separators:
        if separator in instruction:
            instruction = instruction.split(separator)[0]
            break
    instruction = instruction.lower()
    scored: list[dict[str, Any]] = []
    for spec in CAPABILITIES.values():
        hits = [kw for kw in spec.keywords if kw and kw.lower() in lowered]
        unquoted_hits = [kw for kw in hits if kw.lower() in unquoted]
        quoted_only = len(hits) - len(unquoted_hits)
        label = spec.label.lower()
        label_hit = bool(label) and label in unquoted
        label_quoted = bool(label) and not label_hit and label in lowered
        if not hits and not label_hit and not label_quoted:
            continue

        weighted = len(unquoted_hits) + 0.4 * quoted_only
        score = 0.35 + 0.15 * weighted
        core_hits = [kw for kw in unquoted_hits if kw.lower() in instruction]
        if core_hits:
            # 「帮我优化提示词：……」这类指令口吻，命中指令段越多越可信；
            # 只命中一个泛词（如「脚本」）不该盖过命中两个词的能力
            score = max(score, min(0.85, 0.55 + 0.15 * len(core_hits)))
        if label_hit:
            score = max(score, 0.9)
            detail = f"命中能力名称「{spec.label}」"
            if unquoted_hits:
                detail += f"；关键词：{'、'.join(unquoted_hits[:3])}"
        elif label_quoted:
            score = max(score, 0.6)
            detail = f"引号内提到能力「{spec.label}」"
        elif core_hits:
            detail = f"指令提到：{'、'.join(core_hits[:3])}"
        else:
            shown = unquoted_hits or hits
            detail = f"文本包含关键词：{'、'.join(shown[:3])}"

        scored.append({
            "capability": spec.code,
            "label": spec.label,
            "score": round(min(0.95, score), 3),
            "reason": detail,
            "source": "fallback",
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_k]


def fuse_capability_routes(
    ai_routes: list[dict[str, Any]],
    keyword_routes: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    """AI 路由与关键词命中取并集。

    本地小模型经常不看输入、固定回几条候选；关键词命中是文本里的硬证据，
    所以 AI 分数先打六折再与关键词分数比较，命中关键词的能力可以排到前面。
    两者都命中的能力只保留一条，理由优先用关键词证据。
    """
    merged: dict[str, dict[str, Any]] = {}
    for item in ai_routes:
        code = str(item.get("capability") or "")
        if not code:
            continue
        merged[code] = {
            "capability": code,
            "label": str(item.get("label") or label_of(code)),
            "score": round(float(item.get("score") or 0.6) * 0.6, 3),
            "reason": str(item.get("reason") or "AI 判断"),
            "source": "ai",
        }
    for item in keyword_routes:
        code = str(item.get("capability") or "")
        if not code:
            continue
        keyword_score = float(item.get("score") or 0)
        existing = merged.get(code)
        if existing is None:
            merged[code] = {
                "capability": code,
                "label": str(item.get("label") or label_of(code)),
                "score": round(keyword_score, 3),
                "reason": str(item.get("reason") or "关键词命中"),
                "source": "fallback",
            }
        elif keyword_score > existing["score"]:
            existing["score"] = round(keyword_score, 3)
            existing["reason"] = str(item.get("reason") or existing["reason"])
            existing["source"] = "ai+fallback"
    routes = sorted(merged.values(), key=lambda route: float(route["score"]), reverse=True)
    return routes[:top_k]


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
        outcome = chat_with_fallback(
            available_candidates(None), build_messages(prompt), mask_enabled=cloud_mask_enabled(conn)
        )
        prompt_usage.record(conn, "intent-router")
        payload = parse_route_payload(outcome.result.content or "")
        capability_routes = normalize_capability_routes(payload.get("routes") or [], top_k)
        if capability_routes:
            source = "ai"
    except Exception:
        capability_routes = []
    # 关键词命中始终参与排序，避免本地小模型固定回复把真正相关的能力挤掉
    keyword_routes = score_capabilities(text, max(top_k, 6))
    capability_routes = fuse_capability_routes(capability_routes, keyword_routes, top_k)
    if capability_routes and all(item.get("source") == "fallback" for item in capability_routes):
        source = "fallback"

    options = module_options(conn, project_id)
    if options:
        try:
            prompt = registry.render("module-router", {
                "rawText": text,
                "moduleCatalog": _module_catalog_text(options),
            })
            outcome = chat_with_fallback(
                available_candidates(None), build_messages(prompt), mask_enabled=cloud_mask_enabled(conn)
            )
            prompt_usage.record(conn, "module-router")
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


# ---------------- 知识库检索上下文（问答助手用） ----------------

def retrieve_knowledge_context(
    conn: sqlite3.Connection, question: str, limit: int = 5
) -> tuple[str, list[dict[str, Any]], str]:
    """从正式知识库检索上下文，返回 (带 [K1] 标记的正文, 引用清单, 失败原因)。

    第三个元素为空表示检索正常。失败必须和「没检索到」区分开：
    把异常咽下去会让模型在没有任何依据的情况下照常作答。
    """
    from ..repositories.knowledge_repo import KnowledgeRepository

    if not question.strip():
        return "", [], ""
    try:
        result = _search_kb_for_ask(conn, question, KnowledgeRepository, limit)
    except Exception as exc:  # noqa: BLE001 - 检索失败要显式回传，不能伪装成空结果
        logger.warning("知识库检索失败: %s", exc)
        return "", [], f"知识库检索失败（{type(exc).__name__}），本次回答没有引用任何知识条目"
    rows = result.get("list") or []
    if not rows:
        return "", [], ""

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
    return "\n\n".join(blocks), refs, ""


def _search_kb_for_ask(
    conn: sqlite3.Connection, question: str, repo_cls: type[Any], limit: int
) -> dict[str, Any]:
    """问答链路专用检索：复用正式库的表 / 列 / published 过滤，额外开启兜底切词。

    管理页与全局搜索走 KnowledgeRepository.search（不传兜底），语义不变；
    这里只把问答这一条链路的 hybrid_search 打开 fallback_on_empty，
    让「支付回调幂等排查结论是什么样子？」这类自然语言问句也能召回正式条目。
    where_extra 仍强制 review_status='published'，待审版本不会进入检索。
    """
    from ..db.fts import hybrid_search

    return hybrid_search(
        conn,
        fts_table=str(repo_cls.FTS_TABLE),
        content_table=str(repo_cls.table),
        keyword=question,
        columns=list(repo_cls.FTS_COLUMNS),
        extra_where="review_status = 'published'",
        extra_params=(),
        page_num=1,
        page_size=max(1, int(limit)),
        fallback_on_empty=True,
    )
