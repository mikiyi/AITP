"""AI 上下文装配器（GAP-27）。

用例生成过去只喂 5 个变量，模型不知道项目里已有模块树、已有用例、历史缺陷，
于是「第二次生成还是从零写」，产出既不贴业务也大量重复。本模块把这些项目内事实
按能力声明装配成有界的一组上下文块，注入提示词变量。

口径（清单 §5.3 GAP-27 功能点拆解）：

- **召回起点复用既有字面检索**：知识条目走 `db/fts.py` 的 `hybrid_search`，
  用例 / 缺陷 / 功能点走 `instr` 字面命中（与 `services/search_service.py` 同一口径）。
  不新建检索体系、不做向量检索（GAP-27 边界）。
- **三档**：`lite / standard / full`，默认 `standard`（决策 3）。
  每档有硬条目上限与硬字数上限，超限就丢低分块并留痕，绝不无限变厚。
- **可预览**：`preview()` 与装配走同一函数，保证「调用前看到的」与「实际带上的」一致。
- **装配失败不影响主链路**：任何一路召回抛异常都只记一条 note 并继续，
  主链路照常出产出（调用方 `services/lab_service.py` 另有兜底）。

档位解析优先级：请求参数 > 功能点能力绑定 config 的 `contextTier` >
能力声明 `context_tier` > 系统配置 `ai.context.tier` > 默认 `standard`。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..ai.capability_codes import (
    CTX_BUG,
    CTX_CASE,
    CTX_KNOWLEDGE,
    CTX_MODULE,
    CapabilitySpec,
    spec_of,
)
from ..ai.capability_codes import TIER_FULL as CTX_TIER_FULL
from ..ai.capability_codes import TIER_LITE as CTX_TIER_LITE
from ..ai.capability_codes import TIER_STANDARD as CTX_TIER_STANDARD
from ..db.fts import hybrid_search, tokenize
from ..logging_setup import log_event
from ..repositories.module_repo import ModuleRepository
from ..repositories.sys_config_repo import SysConfigRepository

logger = logging.getLogger(__name__)

# ---- 上下文种类（取值定义在 ai/capability_codes.py，能力声明与这里共用一套码）----
KIND_MODULE = CTX_MODULE
KIND_CASE = CTX_CASE
KIND_BUG = CTX_BUG
KIND_KNOWLEDGE = CTX_KNOWLEDGE

CONTEXT_KINDS: tuple[str, ...] = (KIND_MODULE, KIND_CASE, KIND_BUG, KIND_KNOWLEDGE)

KIND_LABELS: dict[str, str] = {
    KIND_MODULE: "模块树路径",
    KIND_CASE: "已有用例",
    KIND_BUG: "历史缺陷",
    KIND_KNOWLEDGE: "知识条目",
}

#: 注入提示词的变量名：模板里以 `{{contextModules}}` 这类占位出现，
#: 且必须在 frontmatter 的 optional 里声明，否则老模板会渲染报错。
VARIABLE_OF_KIND: dict[str, str] = {
    KIND_MODULE: "contextModules",
    KIND_CASE: "contextCases",
    KIND_BUG: "contextBugs",
    KIND_KNOWLEDGE: "contextKnowledge",
}

# ---- 三档（默认 standard，决策 3）；档位码与 capability_codes 的 TIER_* 同源 ----
TIER_LITE = CTX_TIER_LITE
TIER_STANDARD = CTX_TIER_STANDARD
TIER_FULL = CTX_TIER_FULL

CONTEXT_TIERS: tuple[str, ...] = (TIER_LITE, TIER_STANDARD, TIER_FULL)
DEFAULT_TIER = TIER_STANDARD
TIER_LABELS: dict[str, str] = {TIER_LITE: "精简", TIER_STANDARD: "标准", TIER_FULL: "完整"}

#: 每档的硬上限：单种类条目数、总条目数、总字数。
#: 字数上限是 token 成本的主要约束（红线：条数必须硬上限化并可视化）。
TIER_LIMITS: dict[str, dict[str, int]] = {
    TIER_LITE: {"module": 2, "case": 3, "bug": 2, "knowledge": 2, "total": 8, "chars": 900},
    TIER_STANDARD: {"module": 4, "case": 8, "bug": 5, "knowledge": 4, "total": 20, "chars": 2600},
    TIER_FULL: {"module": 8, "case": 16, "bug": 10, "knowledge": 8, "total": 42, "chars": 6000},
}

#: 系统配置里覆盖档位的键
CONFIG_TIER_KEY = "ai.context.tier"
#: 功能点能力绑定 config 里覆盖档位的键
BINDING_TIER_KEY = "contextTier"

#: 参与关键词提取的正文字数上限（长需求先截断，避免切出一堆低价值 token）
MAX_QUERY_CHARS = 1200
#: 召回时最多取多少个关键词（按词长降序）
MAX_KEYWORDS = 8
#: 单块正文进提示词前的字数上限
MAX_BLOCK_CHARS = 260
#: 返回体里最多带多少块（再多只报总数）
MAX_BLOCKS_IN_PAYLOAD = 60

#: 每个能力用哪些变量当检索入口
QUERY_VARIABLES: dict[str, tuple[str, ...]] = {
    "case_generation": ("requirement",),
    "xmind_to_case": ("xmindOutline",),
    "testpoint_generation": ("requirement",),
    "regression_generation": ("changeScope", "affectedModules"),
    "bug_analysis": ("bugTitle", "bugDescription"),
    "case_review": ("requirement", "cases"),
    "api_test_design": ("apiDescription",),
    "automation_script": ("scenario",),
    "performance_script": ("scenario",),
}

_STOP_TOKEN_RE = re.compile(r"^[^\w一-鿿]+$")

#: 装配整体失败时给界面/留痕的说法：能力照常执行，只是这次没带上项目内事实
ASSEMBLY_FAILED_NOTE = "上下文装配失败，本次未注入项目内上下文"


@dataclass(slots=True)
class ContextBlock:
    """一条上下文：种类、来源引用、标题、正文、命中分。"""

    kind: str
    ref: str
    title: str
    text: str
    score: int = 0

    @property
    def chars(self) -> int:
        return len(self.title) + len(self.text)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "kindLabel": KIND_LABELS.get(self.kind, self.kind),
            "ref": self.ref,
            "title": self.title,
            "text": self.text,
            "chars": self.chars,
            "score": self.score,
        }

    def as_prompt_line(self) -> str:
        head = f"- {self.title}" if self.title else "- （无标题）"
        if self.ref:
            head = f"- {self.ref}｜{self.title}" if self.title else f"- {self.ref}"
        body = self.text.strip()
        return f"{head}：{body}" if body else head


@dataclass(slots=True)
class ContextPlan:
    """一次装配的结果；预览、落库、界面统计读的都是它。"""

    capability: str
    tier: str
    project_id: int | None = None
    module_id: int | None = None
    blocks: list[ContextBlock] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    #: 本能力声明要装配的种类；不在这里的种类不注入变量，
    #: 让模板自己按「（未提供）」渲染，避免「没查过」被说成「没查到」。
    kinds: tuple[str, ...] = ()

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.tier, self.tier)

    @property
    def limits(self) -> dict[str, int]:
        return TIER_LIMITS[self.tier]

    def counts_by_kind(self) -> dict[str, int]:
        counts = {kind: 0 for kind in CONTEXT_KINDS}
        for block in self.blocks:
            counts[block.kind] = counts.get(block.kind, 0) + 1
        return counts

    @property
    def chars(self) -> int:
        return sum(block.chars for block in self.blocks)

    @property
    def injected_kinds(self) -> tuple[str, ...]:
        """本次要注入哪些种类：能力没声明的种类不注入，交给模板的「（未提供）」。"""
        declared = tuple(self.kinds)
        if declared:
            return declared
        counts = self.counts_by_kind()
        return tuple(kind for kind in CONTEXT_KINDS if counts.get(kind))

    def variables(self) -> dict[str, str]:
        """提示词注入用的变量表：每个种类一段列表文本。

        查过但没命中时给出「（本次没有召回到相关…）」，让模型明确知道「没有」
        而不是看到空壳；能力没声明的种类一个键都不写，避免「没查过」被说成「没查到」。
        """
        out: dict[str, str] = {}
        for kind in self.injected_kinds:
            name = VARIABLE_OF_KIND.get(kind)
            if not name:
                continue
            picked = [block for block in self.blocks if block.kind == kind]
            if picked:
                out[name] = "\n".join(block.as_prompt_line() for block in picked)
            else:
                out[name] = f"（本次没有召回到相关{KIND_LABELS.get(kind, kind)}）"
        return out

    def as_dict(self) -> dict[str, Any]:
        limits = self.limits
        chars = self.chars
        return {
            "capability": self.capability,
            "tier": self.tier,
            "tierLabel": self.tier_label,
            "projectId": self.project_id,
            "moduleId": self.module_id,
            "kinds": list(self.kinds),
            "keywords": list(self.keywords),
            "itemCount": len(self.blocks),
            "counts": self.counts_by_kind(),
            "limitTotal": limits["total"],
            "limitChars": limits["chars"],
            "chars": chars,
            "estimatedTokens": estimate_tokens(prompt_text(self.variables())),
            "truncated": bool(self.skipped),
            "skipped": list(self.skipped)[:MAX_BLOCKS_IN_PAYLOAD],
            "skippedCount": len(self.skipped),
            "notes": list(self.notes),
            "blocks": [block.as_dict() for block in self.blocks[:MAX_BLOCKS_IN_PAYLOAD]],
        }


def prompt_text(variables: dict[str, str]) -> str:
    """把上下文变量拼成一段文本（估 token 与预览都用它）。"""
    parts: list[str] = []
    for kind in CONTEXT_KINDS:
        name = VARIABLE_OF_KIND[kind]
        value = str(variables.get(name) or "")
        if value:
            parts.append(f"#{KIND_LABELS[kind]}\n{value}")
    return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """粗估 token 数：中日韩字符按 1 token，其余按 4 字符 1 token。

    只用于「上下文让本次调用贵了多少」的可见性与软提示，
    不作为计费依据——计费用模型返回的真实 usage（见 services/cost_service.py）。
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff" or "\u3000" <= char <= "\u303f":
            cjk += 1
        elif not char.isspace():
            other += 1
    return cjk + (other + 3) // 4


def normalize_tier(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in TIER_LIMITS:
        return text
    for tier, label in TIER_LABELS.items():
        if text == label:
            return tier
    return ""


def resolve_tier(
    conn: sqlite3.Connection | None,
    capability: str,
    *,
    requested: Any = None,
    binding_config: dict[str, Any] | None = None,
) -> str:
    """档位解析；非法值一律回落到默认档，不报错。"""
    declared = spec_of(capability)
    for candidate in (
        normalize_tier(requested),
        normalize_tier((binding_config or {}).get(BINDING_TIER_KEY)),
        normalize_tier(declared.context_tier if declared else ""),
    ):
        if candidate:
            return candidate
    if conn is not None:
        try:
            stored = normalize_tier(SysConfigRepository(conn).get_value(CONFIG_TIER_KEY, ""))
        except (sqlite3.Error, KeyError, ValueError) as exc:  # noqa: BLE001 - 配置读不到就用默认档
            log_event(logger, "context_tier_config_failed", level=logging.WARNING,
                      capability=capability, error=str(exc)[:200])
            stored = ""
        if stored:
            return stored
    return DEFAULT_TIER


def query_text_of(spec: CapabilitySpec | None, variables: dict[str, Any]) -> str:
    """取本能力的检索入口文本（需求原文 / 变更范围 / 缺陷描述…）。"""
    names = QUERY_VARIABLES.get(spec.code, ()) if spec else ()
    parts: list[str] = []
    for name in names:
        value = str(variables.get(name) or "").strip()
        if value:
            parts.append(value)
    if not parts and spec:
        # 未登记的能力退回常见需求变量，不至于完全无关键词
        for name in ("requirement", "intent", "rawText"):
            value = str(variables.get(name) or "").strip()
            if value:
                parts.append(value)
    text = "\n".join(parts)
    return text[:MAX_QUERY_CHARS]


def keywords_of(text: str) -> list[str]:
    """从入口文本里取召回关键词：`fts.tokenize` 切词，长词优先，去重限量。"""
    if not text.strip():
        return []
    raw: list[str] = []
    for token in tokenize(text):
        piece = str(token).strip()
        if len(piece) < 2 or _STOP_TOKEN_RE.match(piece):
            continue
        raw.append(piece)
    ordered = sorted(dict.fromkeys(raw), key=lambda item: (-len(item), item))
    return ordered[:MAX_KEYWORDS]


# ---------------- 各路召回 ----------------

def _rank_by_hits(row: dict[str, Any], keywords: list[str], title_keys: Iterable[str],
                  body_keys: Iterable[str]) -> int:
    """字面命中分：标题命中记 2 分、正文命中记 1 分，同分靠时间排序在调用侧处理。"""
    score = 0
    for token in keywords:
        low = token.lower()
        for key in title_keys:
            if low in str(row.get(key) or "").lower():
                score += 2
                break
        for key in body_keys:
            if low in str(row.get(key) or "").lower():
                score += 1
                break
    return score


def _trim(text: Any, limit: int = MAX_BLOCK_CHARS) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return clean[:limit] + ("…" if len(clean) > limit else "")


def _or_match(columns: tuple[str, ...], keywords: list[str]) -> tuple[str, list[str]]:
    """关键词之间 OR（任一词命中即候选）、字段之间 OR 的字面条件，全参数化。

    返回（SQL 片段, 与片段里 ? 一一对应的参数表）；调用方按列数 × 词数展开参数。
    占位符必须按「先每个词遍历所有列」展开，与参数表同序——少一列或多一列都会让
    sqlite 直接报 `Incorrect number of bindings`，装配整路失败。
    """
    if not columns or not keywords:
        return "", []
    clause = " OR ".join(
        f"instr(lower(COALESCE({column}, '')), lower(?)) > 0"
        for _token in keywords for column in columns
    )
    return clause, [token for token in keywords for _ in columns]


def _module_blocks(
    conn: sqlite3.Connection,
    *,
    project_id: int | None,
    module_id: int | None,
    keywords: list[str],
    limit: int,
) -> list[ContextBlock]:
    """模块树路径：绑定了功能点就给它的全路径与子节点，否则按关键词命中功能点。"""
    if limit <= 0 or project_id is None:
        return []
    repo = ModuleRepository(conn)
    blocks: list[ContextBlock] = []
    if module_id is not None:
        path = repo.path_text(int(module_id), project_name="")
        node = repo.get_node(int(module_id))
        if node:
            blocks.append(ContextBlock(
                KIND_MODULE, f"moduleId={module_id}", str(node["name"]),
                _trim(f"完整路径：{path}"), 10,
            ))
        for child in repo.list_children(project_id, int(module_id))[: max(0, limit - 1)]:
            blocks.append(ContextBlock(
                KIND_MODULE, f"moduleId={child['id']}", str(child["name"]),
                _trim(f"下级节点，类型：{child.get('node_type') or ''}"), 6,
            ))
        return blocks[:limit]

    rows = conn.execute(
        "SELECT id, name, node_type FROM biz_module"
        " WHERE is_deleted = 0 AND project_id = ? ORDER BY sort_order, id",
        (project_id,),
    ).fetchall()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for row in rows:
        item = dict(row)
        name = str(item.get("name") or "")
        hit = sum(1 for token in keywords if token.lower() in name.lower())
        if hit:
            scored.append((-hit, int(item["id"]), item))
    for _hit, _id, item in sorted(scored)[:limit]:
        blocks.append(ContextBlock(
            KIND_MODULE, f"moduleId={item['id']}", str(item["name"]),
            _trim(f"层级：{item.get('node_type') or ''}｜"
                  f"路径：{repo.path_text(int(item['id']), project_name='')}"), 5,
        ))
    return blocks


def _case_blocks(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    module_id: int | None,
    requirement_id: int | None,
    keywords: list[str],
    limit: int,
) -> list[ContextBlock]:
    """已有用例摘要：按「同项目（+ 同模块 / 同需求）+ 字面命中」召回。"""
    if limit <= 0 or not keywords:
        return []
    where = ["is_deleted = 0", "project_id = ?"]
    params: list[Any] = [project_id]
    if module_id is not None:
        where.append("module_id = ?")
        params.append(module_id)
    if requirement_id is not None:
        where.append("requirement_id = ?")
        params.append(requirement_id)
    clause, clause_params = _or_match(("title", "tags", "expected_result"), keywords)
    if not clause:
        return []
    where.append(f"({clause})")
    params.extend(clause_params)
    rows = conn.execute(
        "SELECT id, case_no, title, case_type, priority, expected_result, steps_json"
        f" FROM biz_test_case WHERE {' AND '.join(where)}"
        " ORDER BY updated_at DESC, id DESC LIMIT ?",
        [*params, max(limit * 4, limit)],
    ).fetchall()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for row in rows:
        item = dict(row)
        score = _rank_by_hits(item, keywords, ("title",), ("expected_result", "steps_json"))
        if score <= 0:
            continue
        scored.append((-score, -int(item["id"]), item))
    blocks: list[ContextBlock] = []
    for _score, _nid, item in sorted(scored)[:limit]:
        detail = _trim(item.get("expected_result") or item.get("steps_json") or "")
        blocks.append(ContextBlock(
            KIND_CASE, str(item.get("case_no") or f"case:{item['id']}"),
            _trim(item.get("title") or "", 120),
            _trim(f"类型：{item.get('case_type') or ''}｜优先级：{item.get('priority') or ''}"
                  f"｜{detail}"),
                  -_score,
        ))
    return blocks


def _bug_blocks(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    module_id: int | None,
    keywords: list[str],
    limit: int,
) -> list[ContextBlock]:
    """历史缺陷要点：同一套字面召回，正文只留标题与现象摘要。"""
    if limit <= 0 or not keywords:
        return []
    where = ["is_deleted = 0", "project_id = ?"]
    params: list[Any] = [project_id]
    if module_id is not None:
        where.append("module_id = ?")
        params.append(module_id)
    clause, clause_params = _or_match(("title", "description", "module"), keywords)
    if not clause:
        return []
    where.append(f"({clause})")
    params.extend(clause_params)
    rows = conn.execute(
        "SELECT id, bug_no, title, severity, status, description"
        f" FROM biz_bug WHERE {' AND '.join(where)}"
        " ORDER BY updated_at DESC, id DESC LIMIT ?",
        [*params, max(limit * 4, limit)],
    ).fetchall()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for row in rows:
        item = dict(row)
        score = _rank_by_hits(item, keywords, ("title",), ("description",))
        if score <= 0:
            continue
        scored.append((-score, -int(item["id"]), item))
    blocks: list[ContextBlock] = []
    for _score, _nid, item in sorted(scored)[:limit]:
        blocks.append(ContextBlock(
            KIND_BUG, str(item.get("bug_no") or f"bug:{item['id']}"),
            _trim(item.get("title") or "", 120),
            _trim(f"严重级：{item.get('severity') or ''}｜状态：{item.get('status') or ''}"
                  f"｜{item.get('description') or ''}"),
                  -_score,
        ))
    return blocks


def _knowledge_blocks(
    conn: sqlite3.Connection,
    *,
    keywords: list[str],
    limit: int,
) -> list[ContextBlock]:
    """知识条目：复用 `fts.hybrid_search`（字面 + n-gram 混合召回），只取已发布条目。"""
    if limit <= 0:
        return []
    picked: dict[int, tuple[float, dict[str, Any]]] = {}
    for token in keywords[:max(1, limit)]:
        try:
            result = hybrid_search(
                conn,
                fts_table="biz_knowledge_fts",
                content_table="biz_knowledge_article",
                keyword=token,
                columns=["title", "content", "tags"],
                extra_where="review_status = 'published'",
                page_num=1,
                page_size=limit,
                fallback_on_empty=True,
            )
        except sqlite3.Error as exc:  # noqa: BLE001 - 检索失败只影响上下文厚度
            log_event(logger, "context_knowledge_failed", level=logging.WARNING,
                      keyword=token[:40], error=str(exc)[:200])
            continue
        for row in result.get("list") or []:
            article_id = int(row.get("id") or 0)
            if article_id <= 0:
                continue
            score = float(row.get("score") or 0)
            old = picked.get(article_id)
            if old is None or score > old[0]:
                picked[article_id] = (score, dict(row))
    ordered = sorted(picked.items(), key=lambda item: (-item[1][0], item[0]))
    blocks: list[ContextBlock] = []
    for article_id, (score, row) in ordered[:limit]:
        blocks.append(ContextBlock(
            KIND_KNOWLEDGE, f"K:{article_id}",
            _trim(row.get("title") or "", 120),
            _trim(f"分类：{row.get('category') or ''}｜"
                  f"{row.get('summary') or row.get('content') or ''}"),
            int(score),
        ))
    return blocks


def related_requirement_id(
    conn: sqlite3.Connection, project_id: int, keywords: list[str]
) -> int | None:
    """按字面命中找「这次生成对应的是库里哪条需求」；找不到返回 None。

    GAP-29 的去重口径要「同需求」，而能力入口给的是需求原文而不是需求 id，
    所以这里用同一套字面命中把需求对上；对不上就退化成「同项目 + 同模块」。
    """
    if not keywords:
        return None
    clause, clause_params = _or_match(("title", "content"), keywords)
    if not clause:
        return None
    rows = conn.execute(
        "SELECT id, title, content FROM biz_requirement"
        f" WHERE is_deleted = 0 AND project_id = ? AND ({clause})"
        " ORDER BY updated_at DESC, id DESC LIMIT 20",
        [project_id, *clause_params],
    ).fetchall()
    scored: list[tuple[int, int]] = []
    for row in rows:
        item = dict(row)
        score = _rank_by_hits(item, keywords, ("title",), ("content",))
        if score > 0:
            scored.append((score, int(item["id"])))
    if not scored:
        return None
    return min(scored, key=lambda item: (-item[0], item[1]))[1]


# ---------------- 装配入口 ----------------

def assemble(
    conn: sqlite3.Connection,
    *,
    capability: str,
    variables: dict[str, Any],
    project_id: int | None = None,
    module_id: int | None = None,
    requirement_id: int | None = None,
    spec: CapabilitySpec | None = None,
    tier: str | None = None,
    binding_config: dict[str, Any] | None = None,
    requested_tier: Any = None,
) -> ContextPlan:
    """按能力声明装配上下文。能力没声明需要上下文时返回空计划（不调任何检索）。"""
    resolved_spec = spec or spec_of(capability)
    resolved_tier = tier or resolve_tier(
        conn, capability, requested=requested_tier, binding_config=binding_config,
    )
    kinds = tuple(resolved_spec.context_kinds) if resolved_spec else ()
    plan = ContextPlan(
        capability=capability,
        tier=resolved_tier,
        project_id=project_id,
        module_id=module_id,
        kinds=kinds,
    )
    if not kinds:
        plan.notes.append("该能力未声明需要项目上下文，本次不装配")
        return plan

    query = query_text_of(resolved_spec, variables)
    plan.keywords = keywords_of(query)
    if not plan.keywords:
        plan.notes.append("入口文本没有可用关键词，本次只带模块树")

    limits = TIER_LIMITS[plan.tier]
    if project_id is None:
        plan.notes.append("未确定项目（既无 projectId 也无功能点），本次不注入项目内上下文")
        return plan

    # 绑定了功能点时，项目以功能点为准，避免跨项目串数据
    node = ModuleRepository(conn).get_node(int(module_id)) if module_id else None
    if node is not None and int(node.get("project_id") or 0) > 0:
        project_id = int(node["project_id"])
        plan.project_id = project_id

    if requirement_id is None and KIND_CASE in kinds:
        requirement_id = related_requirement_id(conn, project_id, plan.keywords)

    recall: list[tuple[str, list[ContextBlock]]] = []
    for kind in CONTEXT_KINDS:
        if kind not in kinds:
            continue
        limit = int(limits.get(kind, 0))
        try:
            if kind == KIND_MODULE:
                blocks = _module_blocks(conn, project_id=project_id, module_id=module_id,
                                        keywords=plan.keywords, limit=limit)
            elif kind == KIND_CASE:
                blocks = _case_blocks(conn, project_id=project_id, module_id=module_id,
                                      requirement_id=requirement_id,
                                      keywords=plan.keywords, limit=limit)
            elif kind == KIND_BUG:
                blocks = _bug_blocks(conn, project_id=project_id, module_id=module_id,
                                     keywords=plan.keywords, limit=limit)
            else:
                blocks = _knowledge_blocks(conn, keywords=plan.keywords, limit=limit)
        except sqlite3.Error as exc:  # noqa: BLE001 - 单路召回失败不影响其余与主链路
            log_event(logger, "context_recall_failed", level=logging.WARNING,
                      capability=capability, kind=kind, error=str(exc)[:200])
            plan.notes.append(f"{KIND_LABELS[kind]}召回失败，本次未注入（{type(exc).__name__}）")
            blocks = []
        recall.append((kind, blocks[:limit]))

    blocks_in_order: list[ContextBlock] = []
    for kind, blocks in recall:
        blocks_in_order.extend(blocks)
    # 交错排列，避免某一种类独占全部额度
    plan.blocks = _apply_limits(blocks_in_order, plan, limits)
    if len(plan.blocks) < len(blocks_in_order):
        plan.notes.append(
            f"上下文按 {plan.tier_label} 档上限裁剪："
            f"{len(blocks_in_order)} → {len(plan.blocks)} 条"
        )
    return plan


def _apply_limits(
    blocks: list[ContextBlock], plan: ContextPlan, limits: dict[str, int]
) -> list[ContextBlock]:
    """硬上限：总条数 + 总字数；被丢的块留痕，便于界面解释「为什么少了」。"""
    kept: list[ContextBlock] = []
    used_chars = 0
    per_kind: dict[str, int] = {}
    for block in blocks:
        kind_cap = int(limits.get(block.kind, 0))
        if per_kind.get(block.kind, 0) >= kind_cap:
            plan.skipped.append(f"{block.kind}:{block.ref}")
            continue
        if len(kept) >= int(limits["total"]):
            plan.skipped.append(f"{block.kind}:{block.ref}")
            continue
        if used_chars + block.chars > int(limits["chars"]) and kept:
            plan.skipped.append(f"{block.kind}:{block.ref}")
            continue
        kept.append(block)
        used_chars += block.chars
        per_kind[block.kind] = per_kind.get(block.kind, 0) + 1
    return kept


def preview(
    conn: sqlite3.Connection,
    *,
    capability: str,
    variables: dict[str, Any],
    project_id: int | None = None,
    module_id: int | None = None,
    tier: str | None = None,
    binding_config: dict[str, Any] | None = None,
    requested_tier: Any = None,
) -> ContextPlan:
    """调用前预览「本次会带哪些上下文」：与 `assemble` 同一实现，口径必然一致。"""
    return assemble(
        conn,
        capability=capability,
        variables=variables,
        project_id=project_id,
        module_id=module_id,
        tier=tier,
        binding_config=binding_config,
        requested_tier=requested_tier,
    )
