"""生成去重与增量（GAP-29）。

同一需求第二次生成时，模型不知道库里已经有什么，产出与既有用例高度重复。
这里在**生成之后**做一层确定性的相似度判定，把与已有用例（以及本批次内部）
撞车的条目标成「疑似重复」，并支持「以已有用例为基线补充新场景」的增量模式。

判定口径（全部可复算，不依赖模型、不做向量化相似度）：

1. **比较域**：同项目 + 同需求（能对上时）+ 同模块（绑定了功能点时）+ 同用例类型。
   跨用例类型不判重复——边界用例与功能用例步骤相似是常态，判重复就是误报。
2. **文本可比串**：`标题 + 各步骤操作 + 各步骤预期`；比对前先归一
   （转小写、去空白与中英文标点、全角转半角），标题与正文分别算相似度。
3. **综合分** `similarity = max(标题相似度, 0.5 × 标题相似度 + 0.5 × 正文相似度)`，
   相似度用 `difflib.SequenceMatcher.ratio()` 与词集 Jaccard 取较大值。
4. **阈值**：`≥ 0.85` 判「疑似重复」；`≥ 0.95` 或归一后标题完全相同判「确定重复」。
5. **两种模式**：
   - `mark`（默认）：只标记，条目仍在产出里、仍可采纳。所谓「一键恢复」就是把标记
     撤掉照常勾选——本模式下条目从未被丢弃，恢复不需要找回数据，天然可撤销。
   - `increment`（增量模式）：确定重复的条目从产出里摘走（在 `dropped` 里逐条留原文），
     疑似但仍低于确定阈值的照旧只标记；同时给提示词注入基线与「只写新场景」的指令。

字段口径：结果只写进 `DedupReport`，不回写用例条目本身，
所以规范化后的 JSON 不会被多余字段污染；前端按 `items[i]` 与产出数组下标对齐即可。
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable

#: 比对模式
MODE_MARK = "mark"
MODE_INCREMENT = "increment"
MODES: tuple[str, ...] = (MODE_MARK, MODE_INCREMENT)
MODE_LABELS: dict[str, str] = {
    MODE_MARK: "标记疑似重复",
    MODE_INCREMENT: "按基线增量补充",
}

#: 疑似重复阈值 / 确定重复阈值
SUSPECT_THRESHOLD = 0.85
EXACT_THRESHOLD = 0.95

#: 参与比对的已有用例条数上限（比较域再大也只取最近的这些，避免整表比对）
MAX_EXISTING = 60
#: 返回体里最多带多少条判定明细
MAX_ITEMS_IN_PAYLOAD = 60

#: 做过去重标记的能力：产出是「一条条可比对的条目」的能力
DEDUP_CAPABILITIES: dict[str, str] = {
    "case_generation": "用例",
    "xmind_to_case": "用例",
    "regression_generation": "回归点",
}

#: 系统配置 / 功能点绑定里覆盖模式的键（口径与上下文档位一致：请求 > 绑定 > 系统配置 > 默认）
CONFIG_MODE_KEY = "ai.dedup.mode"
BINDING_MODE_KEY = "dedupMode"

_PUNCT_RE = re.compile(r"[\s，。、；：！？,.;:!?\"'“”‘’()（）\[\]{}<>《》/\\|~`@#$%^&*_=+-]+")
#: 全角标点/字母数字（U+FF01–U+FF5E）整体平移半角（U+0021–U+007E），
#: 再加全角空格 U+3000；否则「支付（V2）」和「支付(V2)」会被判成两条不同用例
_FULLWIDTH_MAP = str.maketrans(
    {**{chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}, chr(0x3000): " "}
)


@dataclass(slots=True)
class ComparableCase:
    """一条可比对的文本：编号、标题、正文（标题 + 步骤拼出来的可比串）、类型。"""

    ref: str
    title: str
    body: str
    case_type: str = ""
    index: int | None = None

    @property
    def norm_title(self) -> str:
        return normalize_text(self.title)

    @property
    def norm_body(self) -> str:
        return normalize_text(self.body)


@dataclass(slots=True)
class DuplicateHit:
    """一条判定结果：与谁撞了、像到什么程度、怎么处理。"""

    index: int
    title: str
    similarity: float
    matched_ref: str
    matched_title: str
    reason: str
    scope: str
    source: str  # existing | batch
    dropped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "similarity": round(self.similarity, 3),
            "matchedRef": self.matched_ref,
            "matchedTitle": self.matched_title,
            "reason": self.reason,
            "scope": self.scope,
            "source": self.source,
            "dropped": self.dropped,
            # 「疑似重复」不是删除：标记可撤销，撤销即回到默认勾选
            "restorable": not self.dropped,
        }


@dataclass(slots=True)
class DedupReport:
    """一次去重判定汇总；落库、门禁、界面统计读的都是它。"""

    capability: str
    unit: str
    mode: str
    scope: dict[str, Any] = field(default_factory=dict)
    compared: int = 0
    existing_total: int = 0
    hits: list[DuplicateHit] = field(default_factory=list)
    dropped_items: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def suspected(self) -> list[DuplicateHit]:
        return [hit for hit in self.hits if not hit.dropped]

    @property
    def dropped_count(self) -> int:
        return len(self.dropped_items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "unit": self.unit,
            "mode": self.mode,
            "modeLabel": MODE_LABELS.get(self.mode, self.mode),
            "scope": dict(self.scope),
            "thresholds": {"suspect": SUSPECT_THRESHOLD, "exact": EXACT_THRESHOLD},
            "compared": self.compared,
            "existingTotal": self.existing_total,
            "suspectCount": len(self.suspected),
            "droppedCount": self.dropped_count,
            # 与产出数组下标对齐：前端据此把疑似重复项默认不勾选
            "items": [hit.as_dict() for hit in self.hits[:MAX_ITEMS_IN_PAYLOAD]],
            "hitCount": len(self.hits),
            "dropped": list(self.dropped_items[:MAX_ITEMS_IN_PAYLOAD]),
            "notes": list(self.notes),
        }


def normalize_text(value: Any) -> str:
    """归一化：全角转半角、去空白与标点、转小写。"""
    text = str(value if value is not None else "")
    text = text.translate(_FULLWIDTH_MAP)
    text = _PUNCT_RE.sub("", text)
    return text.lower()


def _token_set(text: str) -> set[str]:
    """词集：连续字母数字段算一个词，其余字符逐字进集，中文再补二字组。

    二字组是为了让「登录超时」和「超时重发」这类共享片段的文本拿到非零 Jaccard，
    单靠逐字切分会把中文压成几乎人人相同的字袋，区分度太低。
    """
    if not text:
        return set()
    tokens: set[str] = set()
    ascii_run: list[str] = []
    for char in text:
        if char.isascii() and char.isalnum():
            ascii_run.append(char)
            continue
        if ascii_run:
            tokens.add("".join(ascii_run))
            ascii_run = []
        tokens.add(char)
    if ascii_run:
        tokens.add("".join(ascii_run))
    for i in range(len(text) - 1):
        pair = text[i:i + 2]
        if not pair.isascii():
            tokens.add(pair)
    return tokens


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sequence = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    set_a, set_b = _token_set(a), _token_set(b)
    union = set_a | set_b
    jaccard = len(set_a & set_b) / len(union) if union else 0.0
    return max(sequence, jaccard)


def similarity(one: ComparableCase, other: ComparableCase) -> float:
    """两条条目的综合相似度（0~1）。"""
    title_sim = _ratio(one.norm_title, other.norm_title)
    body_sim = _ratio(one.norm_body, other.norm_body)
    return max(title_sim, 0.5 * title_sim + 0.5 * body_sim)


def _mode_of(value: Any) -> str:
    """合法就返回，空/非法返回空串——`resolve_mode` 要靠它区分「没填」和「填错」。"""
    text = str(value or "").strip().lower()
    return text if text in MODES else ""


def normalize_mode(value: Any) -> str:
    return _mode_of(value) or MODE_MARK


def mode_or_default(value: Any) -> str:
    """把外部传入的模式收敛成合法值；非法值回落 `mark`（最保守：不丢数据）。"""
    return normalize_mode(value)


def resolve_mode(
    conn: sqlite3.Connection | None,
    capability: str,
    *,
    requested: Any = None,
    binding_config: dict[str, Any] | None = None,
) -> str:
    """去重模式解析：请求参数 > 功能点绑定 > 系统配置 > 默认 `mark`。

    与上下文档位同一套优先级，非法值不报错、直接回落默认，
    免得前端一个拼错的 `incremental` 就把整条能力调用打挂。

    这里必须用 `_mode_of`（没填/填错都返回空串）逐级试，不能用 `normalize_mode`：
    后者对空值也返回 `mark`，会让「功能点绑定」和「系统配置」两级永远读不到，
    优先级链条在第一级就短路。
    """
    for candidate in (
        _mode_of(requested),
        _mode_of((binding_config or {}).get(BINDING_MODE_KEY)),
    ):
        if candidate:
            return candidate
    if conn is not None:
        from ..repositories.sys_config_repo import SysConfigRepository

        try:
            stored = _mode_of(SysConfigRepository(conn).get_value(CONFIG_MODE_KEY, ""))
        except (sqlite3.Error, KeyError, ValueError):
            stored = ""
        if stored:
            return stored
    return MODE_MARK


def is_dedup_capability(capability: str) -> bool:
    return capability in DEDUP_CAPABILITIES


# ---------------- 可比对文本提取 ----------------

def _steps_text(steps: Any) -> str:
    parts: list[str] = []
    if isinstance(steps, str):
        return steps
    for step in steps or []:
        if isinstance(step, dict):
            action = str(step.get("action") or "")
            expected = str(step.get("expected") or "")
            parts.append(f"{action}｜{expected}" if expected else action)
        else:
            parts.append(str(step))
    return "；".join(parts)


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)[:1200]
    except (TypeError, ValueError):
        return str(value)[:1200]


def comparable_of(capability: str, item: Any, index: int) -> ComparableCase | None:
    """把一条产出条目转成可比对文本；不是对象返回 None。"""
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or item.get("name") or "").strip()
    if not title:
        return None
    if capability in ("case_generation", "xmind_to_case"):
        body = "｜".join((
            str(item.get("preconditions") or ""),
            _steps_text(item.get("steps")),
            str(item.get("expectedResult") or ""),
        ))
        case_type = str(item.get("caseType") or "")
    else:
        body = "｜".join((
            str(item.get("module") or ""),
            str(item.get("reason") or ""),
            str(item.get("verifyTarget") or ""),
        ))
        case_type = str(item.get("priority") or "")
    return ComparableCase(
        ref=f"[{index}]", title=title, body=body, case_type=case_type, index=index,
    )


def comparable_from_row(row: dict[str, Any]) -> ComparableCase:
    """把库里的用例 / 回归行转成可比对文本。"""
    title = str(row.get("title") or "").strip()
    if row.get("case_no") is not None or "case_type" in row:
        body = "｜".join((
            str(row.get("preconditions") or ""),
            _steps_text(row.get("steps")),
            str(row.get("expected_result") or ""),
        ))
        case_type = str(row.get("case_type") or "")
    else:
        body = "｜".join((
            str(row.get("module") or ""),
            str(row.get("change_scope") or ""),
            str(row.get("remark") or ""),
        ))
        case_type = str(row.get("priority") or "")
    return ComparableCase(
        ref=str(row.get("no") or row.get("case_no") or row.get("bug_no") or ""),
        title=title,
        body=body,
        case_type=case_type,
    )


# ---------------- 已有条目召回 ----------------

def existing_cases(
    conn: sqlite3.Connection,
    *,
    project_id: int | None,
    module_id: int | None = None,
    requirement_id: int | None = None,
    case_types: Iterable[str] = (),
    limit: int = MAX_EXISTING,
) -> tuple[list[ComparableCase], dict[str, Any]]:
    """按「同项目 + 同模块 + 同需求 + 同用例类型」召回已有用例作为比对基线。

    返回（可比对列表, 比较域描述）；比较域原样进 `DedupReport.scope`，
    界面上能说清「这些是和谁比的」。
    """
    scope: dict[str, Any] = {
        "projectId": project_id,
        "moduleId": module_id,
        "requirementId": requirement_id,
        "caseTypes": sorted({str(item) for item in case_types if str(item)}),
    }
    if project_id is None:
        return [], scope
    where = ["c.is_deleted = 0", "c.project_id = ?"]
    params: list[Any] = [project_id]
    if module_id is not None:
        where.append("c.module_id = ?")
        params.append(module_id)
    if requirement_id is not None:
        where.append("c.requirement_id = ?")
        params.append(requirement_id)
    types = sorted({str(item) for item in case_types if str(item)})
    if types:
        where.append(f"c.case_type IN ({', '.join('?' * len(types))})")
        params.extend(types)
    rows = conn.execute(
        "SELECT c.id, c.case_no, c.title, c.case_type, c.preconditions, c.steps_json,"
        " c.expected_result"
        f" FROM biz_test_case AS c WHERE {' AND '.join(where)}"
        " ORDER BY c.updated_at DESC, c.id DESC LIMIT ?",
        [*params, max(1, min(int(limit or MAX_EXISTING), MAX_EXISTING))],
    ).fetchall()
    picked: list[ComparableCase] = []
    for row in rows:
        item = dict(row)
        try:
            steps = json.loads(item.get("steps_json") or "[]")
        except (TypeError, ValueError):
            steps = []
        picked.append(comparable_from_row({
            "title": item.get("title") or "",
            "case_no": item.get("case_no") or "",
            "case_type": item.get("case_type") or "",
            "preconditions": item.get("preconditions") or "",
            "steps": steps,
            "expected_result": item.get("expected_result") or "",
        }))
    return picked, scope


# ---------------- 判定主流程 ----------------

def _scope_text(scope: dict[str, Any]) -> str:
    parts = [f"projectId={scope.get('projectId')}"]
    if scope.get("moduleId"):
        parts.append(f"moduleId={scope['moduleId']}")
    if scope.get("requirementId"):
        parts.append(f"requirementId={scope['requirementId']}")
    if scope.get("caseTypes"):
        parts.append("caseType=" + "/".join(scope["caseTypes"]))
    return "＋".join(parts)


def _keep(one: ComparableCase, item: Any, kept: list[Any], seen: list[ComparableCase]) -> None:
    """留下一条产出，并把它的下标 / 编号改写成「产出数组下标」。

    `increment` 模式会把确定重复的条目摘走，摘走之后「判定时的原始下标」和
    「它在最终产出里的下标」就错位了；而前端是按 `items[].index` 决定默认不勾选
    哪一行的（`matchedRef` 指向的也是这些留下的条目）。所以留下的条目一律以产出
    下标为准，被摘走的条目才保留原始下标（产出里已经没有它，只能按原样留痕）。
    """
    position = len(kept)
    one.index = position
    one.ref = f"[{position}]"
    seen.append(one)
    kept.append(item)


def dedup_items(
    capability: str,
    items: list[Any],
    existing: list[ComparableCase],
    *,
    mode: str = MODE_MARK,
    scope: dict[str, Any] | None = None,
) -> tuple[list[Any], DedupReport]:
    """对产出条目做去重判定，返回（保留下来的条目, 判定汇总）。

    `mark` 模式永不丢条目；`increment` 模式只丢「确定重复」的。

    下标口径：`hits[].index`（以及 `items[].index`）对**没被摘走**的条目是
    产出数组下标，对**被摘走**的条目是摘走前在原始产出里的下标，与 `dropped[].index`
    一一对应；`increment` 有摘走时两者不在同一套下标里，消费方按 `dropped` 字段区分。
    """
    unit = DEDUP_CAPABILITIES.get(capability, "条目")
    resolved_mode = normalize_mode(mode)
    report = DedupReport(
        capability=capability, unit=unit, mode=resolved_mode,
        scope=dict(scope or {}), compared=len(items), existing_total=len(existing),
    )
    if not items:
        report.notes.append("本次没有可比对的产出条目")
        return items, report

    comparables: list[ComparableCase | None] = [
        comparable_of(capability, item, index) for index, item in enumerate(items)
    ]
    scope_text = _scope_text(report.scope) if report.scope else "未限定比较域"
    kept: list[Any] = []
    seen: list[ComparableCase] = []
    for index, item in enumerate(items):
        one = comparables[index]
        if one is None:
            kept.append(item)
            continue
        best: tuple[float, ComparableCase, str] | None = None
        # ① 与已有条目比对（同类型才比，跨类型判重复就是误报）
        for other in existing:
            if one.case_type and other.case_type and one.case_type != other.case_type:
                continue
            score = similarity(one, other)
            if best is None or score > best[0]:
                best = (score, other, "existing")
        # ② 与本批次已保留的条目比对
        for other in seen:
            if one.case_type and other.case_type and one.case_type != other.case_type:
                continue
            score = similarity(one, other)
            if best is None or score > best[0]:
                best = (score, other, "batch")

        if best is None or best[0] < SUSPECT_THRESHOLD:
            _keep(one, item, kept, seen)
            continue

        score, other, source = best
        exact = score >= EXACT_THRESHOLD or (
            one.norm_title != "" and one.norm_title == other.norm_title
        )
        dropped = exact and resolved_mode == MODE_INCREMENT
        hit = DuplicateHit(
            # 留下的条目：下标取它在产出里的位置；摘走的条目：只能记摘走前的原始位置
            index=index if dropped else len(kept),
            title=one.title,
            similarity=score,
            matched_ref=other.ref or other.title,
            matched_title=other.title,
            reason=(
                "标题归一后与" + ("本批次第 " + str(other.index) + " 条" if source == "batch"
                                  else f"已有 {other.ref}") + "完全相同"
                if one.norm_title == other.norm_title
                else f"标题+步骤综合相似度 {round(score * 100)}%"
            ),
            scope=scope_text if source == "existing" else "本批次内部",
            source=source,
            dropped=dropped,
        )
        if not exact and resolved_mode == MODE_INCREMENT:
            hit.reason += "（低于确定重复阈值，仅标记不摘走）"
        report.hits.append(hit)
        if hit.dropped:
            report.dropped_items.append({
                "index": index, "title": one.title, "raw": _dump(item),
                "matchedRef": hit.matched_ref, "similarity": round(score, 3),
            })
            continue
        _keep(one, item, kept, seen)

    if resolved_mode == MODE_INCREMENT:
        report.notes.append(
            f"增量模式：{report.dropped_count} 条与基线确定重复已摘走，"
            f"{len(report.suspected)} 条仅标记"
        )
    elif report.hits:
        report.notes.append(
            f"标记模式：{len(report.hits)} 条疑似重复仍保留在产出里，可一键恢复勾选"
        )
    return kept, report


def dedup_output(
    conn: sqlite3.Connection,
    capability: str,
    items: list[Any],
    *,
    project_id: int | None,
    module_id: int | None = None,
    requirement_id: int | None = None,
    mode: str = MODE_MARK,
) -> tuple[list[Any], DedupReport]:
    """带库内召回的去重入口：先按比较域取基线，再逐条判定。"""
    if capability not in DEDUP_CAPABILITIES:
        report = DedupReport(capability=capability, unit="条目", mode=normalize_mode(mode))
        report.notes.append("该能力未纳入去重口径，本次不比对")
        return items, report
    types = {str(item.get("caseType") or "") for item in items if isinstance(item, dict)}
    existing, scope = existing_cases(
        conn,
        project_id=project_id,
        module_id=module_id,
        requirement_id=requirement_id if capability != "regression_generation" else None,
        case_types=sorted(t for t in types if t),
    )
    return dedup_items(capability, items, existing, mode=mode, scope=scope)


def baseline_directive(capability: str, items: list[ComparableCase], limit: int = 20) -> str:
    """增量模式给模型的基线说明：列出已有标题，并明确要求只补新场景。"""
    if not items:
        return ""
    titles = "；".join(item.title for item in items[:limit])
    return (
        f"本次为增量补充模式：以下是已有的{'用例' if capability != 'regression_generation' else '回归点'}"
        f"基线（共 {min(len(items), limit)} 条列出），只写它们没有覆盖的新场景，"
        f"标题与步骤都不得与其重复。\n基线：{titles}"
    )
