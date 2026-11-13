"""AI 执行记录：把一次能力执行的结构化结论落库。

一次 AI 执行只留一段原文，没法回答三个问题：它引用的是不是真东西、缺了什么信息、
谁来拍板。这里把结论拆成三份结构化数据写进库：

- **证据**：产出物里引用的知识条目、缺陷编号、用例编号，逐条回库核对是否真实存在。
  编号对不上就是编造，直接标成未验证，并计入风险点。
- **缺项**：模型自述的信息不足处，以及本次请求里没填的可选变量。
- **复核结论**：是否需要人工复核，以及人工复核后的结论（采纳 / 驳回 + 备注）。

GAP-27~30 之后，同一条记录还要回答四个新问题，各自占一列 JSON：

- `context_json`：本次往提示词里装了哪些项目内事实、什么档位、裁掉了多少；
- `schema_json`：产出物按字段规范校验的结论（通过 / 拦下多少条、是否自动修正过）；
- `dedup_json`：与已有条目 / 本批次内部的撞车判定，增量模式摘走了哪些；
- `cost_micros` + `cost_currency`：按档案单价折算的本次花费（微元整数，未配价为 NULL）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, cast

from ..ai.capability_codes import label_of
from ..ai import cost as ai_cost
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode

# ---- 风险等级 ----
RISK_NONE = "none"
RISK_LOW = "low"
RISK_HIGH = "high"

# ---- 复核状态 ----
REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"
REVIEW_STATUSES = (REVIEW_PENDING, REVIEW_APPROVED, REVIEW_REJECTED)

#: 引用标记：[K1] 指向本次知识库检索结果
_K_MARKER_RE = re.compile(r"\[K(\d+)\]")
#: 业务编号
_BUG_NO_RE = re.compile(r"\bBUG-\d+\b", re.IGNORECASE)
_CASE_NO_RE = re.compile(r"\b(?:FT|IT|BT|ST|UIT|ET)-\d+\b", re.IGNORECASE)
_REQ_NO_RE = re.compile(r"\bREQ-\d+\b", re.IGNORECASE)

#: 模型自述缺项的说法
_INSUFFICIENT_RE = re.compile(
    r"(现有信息不足[^。；\n]*|信息不足[^。；\n]*|无法判断[^。；\n]*|无法确认[^。；\n]*"
    r"|还需要[^。；\n]{0,40}|请(?:补充|提供)[^。；\n]{0,40}|缺少[^。；\n]{0,40})"
)
#: 批准性结论：AI 不得替人做上线决策
_APPROVAL_RE = re.compile(
    r"(可以上线|建议上线|批准上线|可以发布|建议发布|已通过验收|可以验收通过|可以合并|建议合并)"
)
#: 高风险关键词
_HIGH_RISK_RE = re.compile(r"\bP[01]\b")
#: Prompt 测试结果
_PROMPT_TEST_RE = re.compile(r"(提示词|prompt|指令)\s*(测试|回归|评测)", re.IGNORECASE)

_MAX_ITEMS = 20
_MAX_EXCERPT = 200

#: 允许写入 biz_ai_record 的列（白名单）
_RECORD_COLUMNS = (
    "task_id",
    "session_id",
    "capability",
    "capability_label",
    "model",
    "summary",
    "output_text",
    "evidence_json",
    "missing_json",
    "risk_json",
    "risk_level",
    "review_required",
    "review_status",
    "input_json",
    "route_json",
    "usage_json",
    "prompt_code",
    "latency_ms",
    "steps_json",
    "mask_json",
    # GAP-27/28/29/30：装配结论、字段校验结论、去重判定、本次折算成本
    "context_json",
    "schema_json",
    "dedup_json",
    "cost_micros",
    "cost_currency",
    "created_at",
    "updated_at",
)


def _excerpt(text: str, limit: int = _MAX_EXCERPT) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:limit]


def _short_summary(output: str) -> str:
    """取产出物的第一行有效内容作为一句话摘要。"""
    for line in (output or "").splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean:
            return _excerpt(clean, 160)
    return ""


# ---------------- 证据 ----------------

def _exists(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> bool:
    return conn.execute(sql, params).fetchone() is not None


def extract_evidence(
    conn: sqlite3.Connection,
    output: str,
    refs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """从产出物里抽出引用，并回库核对是否真实存在。"""
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, ref: str, title: str, verified: bool, detail: str) -> None:
        key = (kind, ref)
        if key in seen or len(evidence) >= _MAX_ITEMS:
            return
        seen.add(key)
        evidence.append({
            "kind": kind,
            "ref": ref,
            "title": title,
            "verified": verified,
            "detail": detail,
        })

    text = output or ""

    # 知识引用：必须能在本次检索结果里对上，对不上就是编造
    ref_map = {
        str(item.get("marker")): item
        for item in (refs or [])
        if item.get("marker")
    }
    for match in _K_MARKER_RE.finditer(text):
        marker = f"K{match.group(1)}"
        hit = ref_map.get(marker)
        if hit:
            add("knowledge", marker, str(hit.get("title") or ""), True, "命中本次知识库检索结果")
        else:
            add("knowledge", marker, "", False, "引用标记在本次检索结果中不存在")

    # 缺陷编号：回库核对
    for raw in dict.fromkeys(_BUG_NO_RE.findall(text)):
        no = raw.upper()
        row = conn.execute(
            "SELECT title FROM biz_bug WHERE bug_no = ? AND is_deleted = 0", (no,)
        ).fetchone()
        if row:
            add("bug", no, str(row["title"]), True, "缺陷编号真实存在")
        else:
            add("bug", no, "", False, "库里没有这个缺陷编号")

    # 用例编号：回库核对
    for raw in dict.fromkeys(_CASE_NO_RE.findall(text)):
        no = raw.upper()
        row = conn.execute(
            "SELECT title FROM biz_test_case WHERE case_no = ? AND is_deleted = 0", (no,)
        ).fetchone()
        if row:
            add("case", no, str(row["title"]), True, "用例编号真实存在")
        else:
            add("case", no, "", False, "库里没有这个用例编号")

    # 需求编号：回库核对
    for raw in dict.fromkeys(_REQ_NO_RE.findall(text)):
        no = raw.upper()
        row = conn.execute(
            "SELECT title FROM biz_requirement WHERE req_no = ? AND is_deleted = 0", (no,)
        ).fetchone()
        if row:
            add("requirement", no, str(row["title"]), True, "需求编号真实存在")
        else:
            add("requirement", no, "", False, "库里没有这个需求编号")

    return evidence


# ---------------- 缺项 ----------------

def extract_missing(
    output: str,
    variables: dict[str, Any] | None = None,
    optional_names: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """模型自述的缺项 + 本次没填的可选变量。"""
    missing: list[dict[str, Any]] = []
    seen: set[str] = set()

    for match in _INSUFFICIENT_RE.finditer(output or ""):
        detail = _excerpt(match.group(0))
        if not detail or detail in seen:
            continue
        seen.add(detail)
        missing.append({"kind": "model", "label": "信息不足", "detail": detail})
        if len(missing) >= _MAX_ITEMS:
            break

    provided = variables or {}
    for name in optional_names:
        if str(provided.get(name, "") or "").strip():
            continue
        label = f"未提供 {name}"
        if label in seen:
            continue
        seen.add(label)
        missing.append({"kind": "input", "label": label, "detail": "该可选信息本次没有提供，结论可能受限"})

    return missing[:_MAX_ITEMS]


# ---------------- 风险点 ----------------

def detect_risks(
    output: str,
    evidence: list[dict[str, Any]],
    missing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按行为安全规则逐条判定命中情况。"""
    risks: list[dict[str, Any]] = []
    text = output or ""

    approval = _APPROVAL_RE.search(text)
    if approval:
        risks.append({
            "rule": "approval",
            "level": RISK_HIGH,
            "title": "出现批准性结论",
            "detail": f"产出物含「{approval.group(0)}」，AI 不得代替人工做上线决策",
        })

    if _HIGH_RISK_RE.search(text):
        risks.append({
            "rule": "high_risk",
            "level": RISK_HIGH,
            "title": "涉及 P0/P1",
            "detail": "涉及 P0/P1 场景，必须人工复核",
        })

    if _PROMPT_TEST_RE.search(text):
        risks.append({
            "rule": "prompt_test",
            "level": RISK_HIGH,
            "title": "Prompt 测试结果",
            "detail": "提示词测试结果必须人工复核",
        })

    fabricated = [item for item in evidence if not item.get("verified")]
    if fabricated:
        risks.append({
            "rule": "fabrication",
            "level": RISK_HIGH,
            "title": "引用无法核实",
            "detail": "引用了库里不存在的条目："
                      + "、".join(str(item.get("ref")) for item in fabricated[:5]),
        })

    if missing:
        risks.append({
            "rule": "insufficient",
            "level": RISK_LOW,
            "title": "信息不足",
            "detail": f"共 {len(missing)} 处信息不足或未提供",
        })

    return risks


def risk_level_of(risks: list[dict[str, Any]]) -> str:
    if any(item.get("level") == RISK_HIGH for item in risks):
        return RISK_HIGH
    if risks:
        return RISK_LOW
    return RISK_NONE


# ---------------- 组装与落库 ----------------

def build_record(
    conn: sqlite3.Connection,
    *,
    capability: str,
    output: str,
    variables: dict[str, Any] | None = None,
    refs: list[dict[str, Any]] | None = None,
    review_required: bool = False,
    model: str = "",
    task_id: int | None = None,
    session_id: int | None = None,
    optional_names: tuple[str, ...] = (),
    route: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    prompt_code: str = "",
    latency_ms: int | None = None,
    steps: list[dict[str, Any]] | None = None,
    mask: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    dedup: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装一条结构化执行记录（尚未落库）。"""
    evidence = extract_evidence(conn, output, refs)
    missing = extract_missing(output, variables, optional_names)
    risks = detect_risks(output, evidence, missing)
    level = risk_level_of(risks)
    # 高风险一定需要人工复核；模型输出已带复核标记时也保留
    need_review = bool(review_required) or level == RISK_HIGH
    return {
        "taskId": task_id,
        "sessionId": session_id,
        "capability": capability,
        "capabilityLabel": label_of(capability),
        "model": model,
        "summary": _short_summary(output),
        "output": output,
        "evidence": evidence,
        "missing": missing,
        "risks": risks,
        "riskLevel": level,
        "reviewRequired": need_review,
        # 补四段：原始输入 / 路由命中 / 步骤轨迹 / 模型用量
        "input": dict(variables or {}),
        "route": list(route or []),
        "steps": list(steps or []),
        "usage": dict(usage or {}),
        "promptCode": prompt_code,
        "latencyMs": latency_ms,
        # 只记类型与数量，绝不落原始输入，避免脱敏前的敏感内容随记录再次扩散
        "mask": dict(mask or {}),
        # GAP-27/28/29/30 四份结论：事后能回答「模型当时看到什么、字段是否合规、
        # 有没有和已有条目撞车、这一次花了多少钱」
        "context": dict(context or {}),
        "schema": dict(schema or {}),
        "dedup": dict(dedup or {}),
        "cost": dict(cost or {}),
    }


def create_record(conn: sqlite3.Connection, record: dict[str, Any]) -> int:
    """把结构化记录写进库，返回记录 id。"""
    now = utcnow_iso()
    payload: dict[str, Any] = {
        "task_id": record.get("taskId"),
        "session_id": record.get("sessionId"),
        "capability": str(record.get("capability") or ""),
        "capability_label": str(record.get("capabilityLabel") or ""),
        "model": str(record.get("model") or ""),
        "summary": str(record.get("summary") or ""),
        "output_text": str(record.get("output") or ""),
        "evidence_json": _dump(record.get("evidence") or []),
        "missing_json": _dump(record.get("missing") or []),
        "risk_json": _dump(record.get("risks") or []),
        "risk_level": str(record.get("riskLevel") or RISK_NONE),
        "review_required": 1 if record.get("reviewRequired") else 0,
        "review_status": REVIEW_PENDING,
        "input_json": _dump(record.get("input") or {}),
        "route_json": _dump(record.get("route") or []),
        "usage_json": _dump(record.get("usage") or {}),
        "prompt_code": str(record.get("promptCode") or ""),
        "mask_json": _dump(record.get("mask") or {}),
        "context_json": _dump(record.get("context") or {}),
        "schema_json": _dump(record.get("schema") or {}),
        "dedup_json": _dump(record.get("dedup") or {}),
        "cost_currency": str((record.get("cost") or {}).get("currency") or ""),
        "created_at": now,
        "updated_at": now,
    }
    latency = record.get("latencyMs")
    if latency is not None:
        payload["latency_ms"] = int(latency)
    steps = record.get("steps")
    if steps:
        payload["steps_json"] = _dump(steps)
    # 没配单价时 cost_micros 留 NULL，而不是写 0——0 会被汇总统计当成「免费」计入
    cost_micros = (record.get("cost") or {}).get("costMicros")
    # Token Plan：套餐模式不按量计金额（强制 NULL），并在落库后按 totalTokens 扣减额度
    profile_id = (record.get("cost") or {}).get("profileId")
    token_plan = False
    if profile_id:
        mode_row = conn.execute(
            "SELECT billing_mode FROM sys_ai_profile WHERE id = ?", (int(profile_id),)
        ).fetchone()
        token_plan = bool(mode_row) and str(mode_row[0] or "api") == "token_plan"
    if token_plan:
        cost_micros = None
    if cost_micros is not None:
        payload["cost_micros"] = int(cost_micros)
    # 只落库有值的字段：列名走白名单，值走命名参数，不拼 SQL 片段
    columns = [name for name in _RECORD_COLUMNS if name in payload]
    if not columns:
        raise AppError(ErrorCode.PARAM_ERROR, "执行记录缺少必填字段")
    placeholders = ", ".join(f":{name}" for name in columns)
    cursor = conn.execute(
        f"INSERT INTO biz_ai_record ({', '.join(columns)}) VALUES ({placeholders})",
        {name: payload[name] for name in columns},
    )
    record_id = int(cast(int, cursor.lastrowid))
    if token_plan:
        total = int((record.get("usage") or {}).get("totalTokens") or 0)
        ai_cost.deduct_token_plan(conn, profile_id, total)
    return record_id


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def apply_review(
    conn: sqlite3.Connection,
    record_id: int,
    status: str,
    note: str = "",
) -> dict[str, Any]:
    """写入人工复核结论。"""
    if status not in REVIEW_STATUSES or status == REVIEW_PENDING:
        raise AppError(ErrorCode.PARAM_ERROR, "复核结论只能是 approved 或 rejected")
    row = conn.execute(
        "SELECT id, capability, review_status FROM biz_ai_record WHERE id = ? AND is_deleted = 0",
        (record_id,),
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不存在")
    now = utcnow_iso()
    conn.execute(
        "UPDATE biz_ai_record SET review_status = ?, review_note = ?, reviewed_at = ?,"
        " updated_at = ? WHERE id = ?",
        (status, note, now, now, record_id),
    )
    return {
        "id": record_id,
        "reviewStatus": status,
        "reviewNote": note,
        "reviewedAt": now,
    }
