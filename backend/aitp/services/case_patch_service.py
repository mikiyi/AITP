"""用例局部 Diff 修订（V2.6.0）。

对一批已有用例下达一条自然语言指令（补登录异常、删重复、改预期、重排步骤），
模型只产出「逐条 patch」，不改库；用户在列表里逐条采纳或忽略，采纳后才写回
目标字段并留审计。

与修复建议（单条执行失败 → 单条资产修改）的区别：这里面向用例集做增量编辑，
产出是 patch 列表，适合日常维护而不是故障处置。
"""

from __future__ import annotations

import difflib
import json
import sqlite3
from typing import Any, Callable

from ..common.enums import CASE_PRIORITY
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.base import dumps, loads
from ..repositories.numbering import next_no_monotonic
from ..repositories.test_case_repo import TestCaseRepository
from . import audit_service, xlsx_importer

PATCH_PREFIX = "AI_PATCH"
RESOURCE = "case_patch"

ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTIONS = (ACTION_UPDATE, ACTION_DELETE)
ACTION_LABELS = {ACTION_UPDATE: "修改用例", ACTION_DELETE: "删除用例（重复/废弃）"}

TARGET_TITLE = "title"
TARGET_STEPS = "steps"
TARGET_EXPECTED = "expected_result"
TARGET_PRECONDITIONS = "preconditions"
TARGET_PRIORITY = "priority"
TARGETS = (TARGET_TITLE, TARGET_STEPS, TARGET_EXPECTED, TARGET_PRECONDITIONS, TARGET_PRIORITY)
TARGET_LABELS = {
    TARGET_TITLE: "用例标题",
    TARGET_STEPS: "测试步骤",
    TARGET_EXPECTED: "预期结果",
    TARGET_PRECONDITIONS: "前置条件",
    TARGET_PRIORITY: "优先级",
}

STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_DISMISSED = "dismissed"
STATUSES = (STATUS_PENDING, STATUS_APPLIED, STATUS_DISMISSED)

BATCH_PENDING = "pending"
BATCH_PARTIAL = "partial"
BATCH_APPLIED = "applied"
BATCH_DISMISSED = "dismissed"
BATCH_LABELS = {
    BATCH_PENDING: "待审",
    BATCH_PARTIAL: "部分处理",
    BATCH_APPLIED: "已采纳",
    BATCH_DISMISSED: "已忽略",
}

MAX_CASES = 80
NO_MODEL_MESSAGE = "无可用 AI 配置，无法生成用例修订建议；可手工编辑用例"

Generator = Callable[[dict[str, Any]], dict[str, Any]]


def _case_blocks(conn: sqlite3.Connection, project_id: int, case_ids: list[int]) -> list[dict[str, Any]]:
    ids = sorted({int(item) for item in case_ids if int(item) > 0})
    if not ids:
        raise AppError(ErrorCode.PARAM_ERROR, "请至少选择一条用例")
    if len(ids) > MAX_CASES:
        raise AppError(ErrorCode.PARAM_ERROR, f"一次最多修订 {MAX_CASES} 条用例")
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM biz_test_case WHERE id IN ({placeholders}) AND is_deleted = 0",
        ids,
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if int(row["project_id"]) != int(project_id):
            continue
        steps = loads(row["steps_json"], []) or []
        out.append({
            "caseId": int(row["id"]),
            "caseNo": str(row["case_no"] or ""),
            "title": str(row["title"] or ""),
            "priority": str(row["priority"] or "P2"),
            "preconditions": str(row["preconditions"] or ""),
            "steps": [
                f"{item.get('action') or ''}｜{item.get('expected') or ''}"
                if isinstance(item, dict) else str(item)
                for item in steps
            ],
            "expectedResult": str(row["expected_result"] or ""),
        })
    if not out:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "所选用例不存在或不属于该项目")
    return out


def build_variables(
    conn: sqlite3.Connection, *, project_id: int, case_ids: list[int], instruction: str,
) -> dict[str, Any]:
    cases = _case_blocks(conn, project_id, case_ids)
    project = conn.execute(
        "SELECT name FROM biz_project WHERE id = ?", (int(project_id),),
    ).fetchone()
    return {
        "projectName": str(project["name"] or "") if project is not None else "",
        "instruction": str(instruction or "").strip() or "按测试规范补全并精简这批用例",
        "casesJson": json.dumps(cases, ensure_ascii=False),
        "caseCount": str(len(cases)),
    }


def _default_generator(conn: sqlite3.Connection, project_id: int,
                       profile_id: int | None, images: list[str] | None = None) -> Generator:
    def run(variables: dict[str, Any]) -> dict[str, Any]:
        from . import lab_service

        return lab_service.run_capability(
            conn, "case_patch", variables, project_id=project_id, profile_id=profile_id,
            images=images or [],
        ).as_dict()

    return run


def _before_text(case: dict[str, Any], target: str) -> str:
    if target == TARGET_TITLE:
        return str(case.get("title") or "")
    if target == TARGET_STEPS:
        return "\n".join(str(item) for item in (case.get("steps") or []))
    if target == TARGET_EXPECTED:
        return str(case.get("expectedResult") or "")
    if target == TARGET_PRECONDITIONS:
        return str(case.get("preconditions") or "")
    if target == TARGET_PRIORITY:
        return str(case.get("priority") or "")
    return ""


def _diff(before: str, after: str) -> str:
    if not after.strip():
        return ""
    return "\n".join(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile="当前内容", tofile="修订后", lineterm="",
    ))


def _resolve_cases(conn: sqlite3.Connection, project_id: int,
                   items: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    numeric = sorted({int(item.get("caseId") or 0) for item in items
                      if str(item.get("caseId") or "").isdigit()})
    case_nos = sorted({str(item.get("caseId") or "") for item in items
                       if item.get("caseId") and not str(item.get("caseId")).isdigit()})
    blocks: list[dict[str, Any]] = []
    if numeric:
        blocks.extend(_case_blocks(conn, project_id, numeric))
    if case_nos:
        placeholders = ",".join("?" for _ in case_nos)
        rows = conn.execute(
            f"SELECT id FROM biz_test_case WHERE project_id = ? AND is_deleted = 0"
            f" AND case_no IN ({placeholders})",
            [int(project_id), *case_nos],
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            blocks.extend(_case_blocks(conn, project_id, ids))
    by_id = {int(block["caseId"]): block for block in blocks}
    by_no = {str(block["caseNo"]): block for block in blocks if block["caseNo"]}
    return by_id, by_no


def generate(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    case_ids: list[int],
    instruction: str,
    profile_id: int | None = None,
    generator: Generator | None = None,
    actor: str = "local",
    attachment_ids: list[str] | None = None,
) -> dict[str, Any]:
    """生成一批待审 patch；无模型时只返回提示，不落库。

    ``attachment_ids`` 支持补充截图等图片上下文，随多模态消息一起送给模型；
    文档附件则由统一解析层拼进指令文本。
    """
    from . import ai_input_service

    resolved = ai_input_service.resolve(conn, attachment_ids)
    instruction_text = ai_input_service.compose_text(instruction, resolved)
    variables = build_variables(
        conn, project_id=project_id, case_ids=case_ids, instruction=instruction_text,
    )
    run = generator or _default_generator(
        conn, project_id, profile_id, images=resolved.images)
    try:
        outcome = run(variables)
    except AppError as exc:
        if exc.code == ErrorCode.AI_CREDENTIAL_MISSING:
            return {"generated": False, "message": NO_MODEL_MESSAGE, "patches": []}
        raise

    parsed = outcome.get("parsed")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("patches"), list):
        return {
            "generated": False,
            "message": "模型输出不是合法的 patch 结构，请重试",
            "patches": [],
            "raw": str(outcome.get("raw") or "")[:2000],
        }

    raw_items = [item for item in parsed["patches"] if isinstance(item, dict)][:MAX_CASES * 2]
    by_id, by_no = _resolve_cases(conn, project_id, raw_items)
    batch_no = next_no_monotonic(conn, "biz_case_patch", "patch_no", PATCH_PREFIX)
    now = utcnow_iso()
    cursor = conn.execute(
        """
        INSERT INTO biz_case_patch
            (patch_no, project_id, instruction, status, model, usage_json, cost_json,
             degrade_json, created_by, created_at, updated_at, is_deleted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            batch_no, int(project_id), variables["instruction"], BATCH_PENDING,
            str(outcome.get("model") or ""), dumps(outcome.get("usage") or {}),
            dumps(outcome.get("cost") or {}), dumps(outcome.get("degrade") or {}),
            actor[:64], now, now,
        ),
    )
    patch_id = int(cursor.lastrowid or 0)

    created = 0
    for item in raw_items:
        raw_key = str(item.get("caseId") or "")
        case_id = int(raw_key) if raw_key.isdigit() else 0
        block = by_id.get(case_id) or by_no.get(raw_key)
        if block is None:
            continue
        action = str(item.get("action") or ACTION_UPDATE)
        if action not in ACTIONS:
            action = ACTION_UPDATE
        target = str(item.get("target") or TARGET_STEPS)
        if target not in TARGETS:
            target = TARGET_STEPS
        after = str(item.get("after") or "").strip()
        if action == ACTION_UPDATE and not after:
            continue
        before = _before_text(block, target)
        if action == ACTION_UPDATE and after == before:
            continue
        conn.execute(
            """
            INSERT INTO biz_case_patch_item
                (patch_id, project_id, case_id, case_no, case_title, action, target,
                 before_text, after_text, reason, status, created_at, updated_at, is_deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                patch_id, int(project_id), case_id, block["caseNo"], block["title"],
                action, target, before[:4000], after[:4000],
                str(item.get("reason") or "")[:1000], STATUS_PENDING, now, now,
            ),
        )
        created += 1
    if created == 0:
        conn.execute("DELETE FROM biz_case_patch WHERE id = ?", (patch_id,))
        return {"generated": False, "message": "模型没有给出可落地的修改，请换一种指令", "patches": []}
    return get_patch(conn, patch_id, project_id=project_id)


def _item_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "patchId": int(row["patch_id"]),
        "caseId": int(row["case_id"]),
        "caseNo": str(row["case_no"] or ""),
        "caseTitle": str(row["case_title"] or ""),
        "action": str(row["action"] or ACTION_UPDATE),
        "actionLabel": ACTION_LABELS.get(str(row["action"]), str(row["action"])),
        "target": str(row["target"] or TARGET_STEPS),
        "targetLabel": TARGET_LABELS.get(str(row["target"]), str(row["target"])),
        "before": str(row["before_text"] or ""),
        "after": str(row["after_text"] or ""),
        "diff": _diff(str(row["before_text"] or ""), str(row["after_text"] or "")),
        "reason": str(row["reason"] or ""),
        "status": str(row["status"] or STATUS_PENDING),
        "statusLabel": {STATUS_PENDING: "待审", STATUS_APPLIED: "已采纳",
                        STATUS_DISMISSED: "已忽略"}.get(str(row["status"]), str(row["status"])),
        "appliedAt": str(row["applied_at"] or ""),
        "dismissedAt": str(row["dismissed_at"] or ""),
    }


def get_patch(conn: sqlite3.Connection, patch_id: int,
              *, project_id: int | None = None) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM biz_case_patch WHERE id = ? AND is_deleted = 0", (int(patch_id),),
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "修订批次不存在")
    if project_id is not None and int(row["project_id"]) != int(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "修订批次不属于该项目")
    items = conn.execute(
        "SELECT * FROM biz_case_patch_item WHERE patch_id = ? AND is_deleted = 0 ORDER BY id",
        (int(patch_id),),
    ).fetchall()
    payload_items = [_item_payload(item) for item in items]
    counts = {status: 0 for status in STATUSES}
    for item in payload_items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "id": int(row["id"]),
        "patchNo": str(row["patch_no"] or ""),
        "projectId": int(row["project_id"]),
        "instruction": str(row["instruction"] or ""),
        "status": str(row["status"] or BATCH_PENDING),
        "statusLabel": BATCH_LABELS.get(str(row["status"]), str(row["status"])),
        "model": str(row["model"] or ""),
        "usage": loads(row["usage_json"], {}) or {},
        "cost": loads(row["cost_json"], {}) or {},
        "degrade": loads(row["degrade_json"], {}) or {},
        "items": payload_items,
        "counts": counts,
        "createdAt": str(row["created_at"] or ""),
    }


def list_patches(
    conn: sqlite3.Connection, *, project_id: int, limit: int = 20,
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, patch_no, instruction, status, model, created_at FROM biz_case_patch"
        " WHERE project_id = ? AND is_deleted = 0 ORDER BY id DESC LIMIT ?",
        (int(project_id), max(1, min(100, int(limit)))),
    ).fetchall()
    items = []
    for row in rows:
        counts = conn.execute(
            "SELECT status, COUNT(*) AS c FROM biz_case_patch_item"
            " WHERE patch_id = ? AND is_deleted = 0 GROUP BY status",
            (int(row["id"]),),
        ).fetchall()
        items.append({
            "id": int(row["id"]),
            "patchNo": str(row["patch_no"] or ""),
            "instruction": str(row["instruction"] or ""),
            "status": str(row["status"] or BATCH_PENDING),
            "statusLabel": BATCH_LABELS.get(str(row["status"]), str(row["status"])),
            "model": str(row["model"] or ""),
            "createdAt": str(row["created_at"] or ""),
            "itemCount": sum(int(item["c"]) for item in counts),
        })
    return {"list": items, "total": len(items)}


def _require_pending_item(
    conn: sqlite3.Connection, item_id: int, project_id: int,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM biz_case_patch_item WHERE id = ? AND is_deleted = 0", (int(item_id),),
    ).fetchone()
    if row is None or int(row["project_id"]) != int(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "修订条目不存在")
    if str(row["status"]) != STATUS_PENDING:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "该条目已是终态，不能重复处理")
    return dict(row)


def _sync_batch_status(conn: sqlite3.Connection, patch_id: int) -> None:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM biz_case_patch_item"
        " WHERE patch_id = ? AND is_deleted = 0 GROUP BY status",
        (int(patch_id),),
    ).fetchall()
    counts = {str(row["status"]): int(row["c"]) for row in rows}
    if counts.get(STATUS_PENDING):
        status = BATCH_PENDING
    elif counts.get(STATUS_APPLIED) and counts.get(STATUS_DISMISSED):
        status = BATCH_PARTIAL
    elif counts.get(STATUS_APPLIED):
        status = BATCH_APPLIED
    else:
        status = BATCH_DISMISSED
    conn.execute(
        "UPDATE biz_case_patch SET status = ?, updated_at = ? WHERE id = ?",
        (status, utcnow_iso(), int(patch_id)),
    )


def adopt_item(
    conn: sqlite3.Connection, item_id: int, *, project_id: int, actor: str = "local",
) -> dict[str, Any]:
    """采纳一条 patch：写回目标字段（删除类做软删），并留审计。"""
    item = _require_pending_item(conn, item_id, project_id)
    case_id = int(item["case_id"])
    case = TestCaseRepository(conn).get_detail(case_id)
    if case is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "目标用例不存在或已删除")
    now = utcnow_iso()
    action = str(item["action"])
    if action == ACTION_DELETE:
        TestCaseRepository(conn).update(case_id, {"is_deleted": 1})
        field = "is_deleted"
        after = "1"
    else:
        target = str(item["target"])
        after = str(item["after_text"] or "")
        if target == TARGET_PRIORITY and after not in CASE_PRIORITY:
            raise AppError(ErrorCode.PARAM_ERROR, f"优先级只能是 {'/'.join(CASE_PRIORITY)}")
        update: dict[str, Any] = {}
        if target == TARGET_TITLE:
            update["title"] = after
        elif target == TARGET_EXPECTED:
            update["expected_result"] = after
        elif target == TARGET_PRECONDITIONS:
            update["preconditions"] = after
        elif target == TARGET_PRIORITY:
            update["priority"] = after
        elif target == TARGET_STEPS:
            update["steps_json"] = dumps(xlsx_importer.steps_from_text(after))
        else:
            raise AppError(ErrorCode.PARAM_ERROR, "未知的修订目标")
        TestCaseRepository(conn).update(case_id, update)
        field = target
    conn.execute(
        "UPDATE biz_case_patch_item SET status = ?, applied_at = ?, applied_by = ?,"
        " updated_at = ? WHERE id = ?",
        (STATUS_APPLIED, now, actor, now, int(item_id)),
    )
    _sync_batch_status(conn, int(item["patch_id"]))
    audit_service.record(
        conn, audit_service.AUDIT_ACTION_UPDATE, RESOURCE, int(item_id),
        f"{item['case_no']} {item['case_title']}",
        {"caseId": case_id, "field": field, "action": action,
         "before": str(item["before_text"])[:1000], "after": after[:1000]},
        actor=actor,
    )
    return {"applied": True, "item": _reload_item(conn, item_id), "caseId": case_id}


def dismiss_item(
    conn: sqlite3.Connection, item_id: int, *, project_id: int, actor: str = "local",
) -> dict[str, Any]:
    item = _require_pending_item(conn, item_id, project_id)
    now = utcnow_iso()
    conn.execute(
        "UPDATE biz_case_patch_item SET status = ?, dismissed_at = ?, updated_at = ?"
        " WHERE id = ?",
        (STATUS_DISMISSED, now, now, int(item_id)),
    )
    _sync_batch_status(conn, int(item["patch_id"]))
    audit_service.record(
        conn, audit_service.AUDIT_ACTION_UPDATE, RESOURCE, int(item_id),
        f"{item['case_no']} {item['case_title']}",
        {"status": STATUS_DISMISSED, "action": item["action"]}, actor=actor,
    )
    return {"dismissed": True, "item": _reload_item(conn, item_id)}


def _reload_item(conn: sqlite3.Connection, item_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM biz_case_patch_item WHERE id = ?", (int(item_id),),
    ).fetchone()
    return _item_payload(row) if row is not None else {}


__all__ = [
    "generate", "list_patches", "get_patch", "adopt_item", "dismiss_item",
    "PATCH_PREFIX", "TARGETS", "TARGET_LABELS", "ACTIONS", "ACTION_LABELS", "STATUSES",
]
