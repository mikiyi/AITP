"""AI 修复建议服务（四期专项 A）。

接口执行断言失败后，装配失败上下文 → 调模型诊断 → 产出「资产修改建议」进待审池，
人工一键采纳后改写用例的断言 / 预期并留审计。

三条口径：
  - **AI 只建议、人决策**：采纳是显式人工动作，建议一旦 applied / dismissed 即终态，
    不可二次采纳（防止绕过同口径）；
  - **降级不阻塞**：没有可用模型时不生成建议、不抛错，只回机械 diff 供人工比对；
  - **项目硬隔离**：所有读写都按 project_id 收敛，跨项目一律按「不存在」处理。
"""

from __future__ import annotations

import difflib
import json
import sqlite3
from typing import Any, Callable

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.base import dumps, loads
from ..repositories.numbering import next_no_monotonic
from ..repositories.test_case_repo import TestCaseRepository
from . import audit_service

#: 编号前缀（与 BUG- / AI_BUG- / Auto_BUG- 同构：全局自增、软删不复用）
SUGGESTION_PREFIX = "AI_FIX"

#: 可用目标：改断言 / 改预期响应体 / 改预期状态码 / 判定为接口缺陷（转缺陷，AI 不建单）
TARGET_ASSERTION = "assertion"
TARGET_EXPECTED_BODY = "expected_body"
TARGET_EXPECTED_STATUS = "expected_status"
TARGET_BUG = "bug"
TARGETS: tuple[str, ...] = (
    TARGET_ASSERTION, TARGET_EXPECTED_BODY, TARGET_EXPECTED_STATUS, TARGET_BUG,
)
TARGET_LABELS = {
    TARGET_ASSERTION: "修改断言规则",
    TARGET_EXPECTED_BODY: "修改预期响应体",
    TARGET_EXPECTED_STATUS: "修改预期状态码",
    TARGET_BUG: "判定为接口缺陷",
}

STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_DISMISSED = "dismissed"
STATUSES: tuple[str, ...] = (STATUS_PENDING, STATUS_APPLIED, STATUS_DISMISSED)
STATUS_LABELS = {
    STATUS_PENDING: "待审",
    STATUS_APPLIED: "已采纳",
    STATUS_DISMISSED: "已忽略",
}

#: 用例 extra 里与三类建议一一对应的字段名
EXTRA_KEY_ASSERTIONS = "断言规则"
EXTRA_KEY_EXPECTED_BODY = "预期响应体"
EXTRA_KEY_EXPECTED_STATUS = "预期状态码"

#: 生成函数签名：注入点便于门禁替换为桩，不影响生产链路
Generator = Callable[[dict[str, Any]], dict[str, Any]]

_NO_MODEL_MESSAGE = "无可用 AI 配置，仅提供规则 diff 供人工比对"

#: 审计资源类型
RESOURCE = "fix_suggestion"


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    """库行 → 前端可用结构（JSON 列解析、状态中文名）。"""
    suggestion_json = loads(row["suggestion_json"], {}) or {}
    return {
        "id": int(row["id"]),
        "suggestionNo": str(row["suggestion_no"] or ""),
        "projectId": int(row["project_id"]),
        "executionId": int(row["execution_id"]),
        "evidenceId": row["evidence_id"],
        "caseId": int(row["case_id"]),
        "caseNo": str(row["case_no"] or ""),
        "source": str(row["source"] or "ai"),
        "targetType": str(row["target_type"] or TARGET_ASSERTION),
        "targetLabel": TARGET_LABELS.get(str(row["target_type"]), str(row["target_type"])),
        "suggestion": suggestion_json,
        "analysis": str(suggestion_json.get("analysis") or ""),
        "summary": str(suggestion_json.get("suggestion") or ""),
        "reason": str(suggestion_json.get("reason") or ""),
        "diffPreview": str(row["diff_preview"] or ""),
        "status": str(row["status"] or STATUS_PENDING),
        "statusLabel": STATUS_LABELS.get(str(row["status"]), str(row["status"])),
        "model": str(row["model"] or ""),
        "usage": loads(row["usage_json"], {}) or {},
        "cost": loads(row["cost_json"], {}) or {},
        "degrade": loads(row["degrade_json"], {}) or {},
        "appliedAt": str(row["applied_at"] or ""),
        "appliedBy": str(row["applied_by"] or ""),
        "dismissedAt": str(row["dismissed_at"] or ""),
        "createdAt": str(row["created_at"] or ""),
    }


def _load_execution(conn: sqlite3.Connection, execution_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM biz_interface_execution WHERE id = ?", (int(execution_id),)
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不存在")
    return dict(row)


def _failed_assertions(execution: dict[str, Any]) -> list[dict[str, Any]]:
    items = loads(execution.get("assertions_json"), []) or []
    return [item for item in items if isinstance(item, dict) and not item.get("passed")]


def _mechanical_diff(execution: dict[str, Any]) -> str:
    """无模型时的机械对比：逐条失败断言给出「期望 vs 实际」。"""
    lines: list[str] = []
    for item in _failed_assertions(execution):
        name = str(item.get("name") or "断言")
        expected = item.get("expected")
        actual = item.get("actual")
        lines.append(f"- {name}：期望 {expected}，实际 {actual}")
        message = str(item.get("message") or "")
        if message:
            lines.append(f"  说明：{message}")
    if not lines:
        lines.append("- 该执行记录没有失败的断言")
    return "\n".join(lines)


def build_variables(
    conn: sqlite3.Connection, execution: dict[str, Any]
) -> dict[str, Any]:
    """把一次失败执行装配成提示词变量（不含 AI 调用，门禁可直接断言）。"""
    case_row = conn.execute(
        "SELECT case_no, title, extra_json, project_id FROM biz_test_case WHERE id = ?",
        (int(execution["case_id"]),),
    ).fetchone()
    extra = loads(case_row["extra_json"], {}) if case_row is not None else {}
    if not isinstance(extra, dict):
        extra = {}
    case_no = str(case_row["case_no"] or "") if case_row is not None else ""
    case_title = str(case_row["title"] or "") if case_row is not None else ""
    failed = _failed_assertions(execution)
    failed_summary = "\n".join(
        f"- {item.get('name') or '断言'}：期望 {item.get('expected')}，"
        f"实际 {item.get('actual')}；{item.get('message') or ''}"
        for item in failed
    ) or "（本次执行没有失败断言）"
    response = loads(execution.get("response_json"), {}) or {}
    response_summary = json.dumps(
        {
            "statusCode": execution.get("status_code"),
            "body": str(response.get("body") or "")[:4000],
            "bodyTruncated": bool(response.get("bodyTruncated")),
        },
        ensure_ascii=False,
    )
    return {
        "caseNo": case_no,
        "caseTitle": case_title,
        "failedSummary": failed_summary,
        "oldAssertions": str(extra.get(EXTRA_KEY_ASSERTIONS) or "（未配置断言规则）"),
        "oldExpectedBody": str(extra.get(EXTRA_KEY_EXPECTED_BODY) or "（未配置预期响应体）"),
        "oldExpectedStatus": str(extra.get(EXTRA_KEY_EXPECTED_STATUS) or "（未配置预期状态码）"),
        "responseSummary": response_summary,
    }


def _default_generator(conn: sqlite3.Connection, project_id: int | None,
                       profile_id: int | None) -> Generator:
    """生产链路：走 lab_service 能力执行（含 Schema 校验与降级）。"""

    def run(variables: dict[str, Any]) -> dict[str, Any]:
        from . import lab_service

        result = lab_service.run_capability(
            conn,
            "fix_suggestion",
            variables,
            project_id=project_id,
            profile_id=profile_id,
        )
        return result.as_dict()

    return run


def _diff_preview(target: str, old: str, new: str) -> str:
    if not new.strip():
        return ""
    return "\n".join(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile="当前资产", tofile="建议修改", lineterm="",
        )
    )


def _new_value_of(target: str, suggestion: dict[str, Any]) -> str:
    if target == TARGET_ASSERTION:
        return str(suggestion.get("newAssertion") or "").strip()
    if target == TARGET_EXPECTED_BODY:
        return str(suggestion.get("newExpectedBody") or "").strip()
    if target == TARGET_EXPECTED_STATUS:
        return str(suggestion.get("newExpectedStatus") or "").strip()
    return ""


def generate(
    conn: sqlite3.Connection,
    execution_id: int,
    *,
    project_id: int | None = None,
    profile_id: int | None = None,
    generator: Generator | None = None,
    actor: str = "local",
) -> dict[str, Any]:
    """从一次执行记录生成一条修复建议。

    没有可用模型时返回 ``generated=False`` + 机械 diff，不写库、不报错（降级不阻塞）。
    """
    execution = _load_execution(conn, execution_id)
    if str(execution.get("result") or "") == "passed":
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "该次执行已通过，无需生成修复建议")

    case_row = conn.execute(
        "SELECT project_id FROM biz_test_case WHERE id = ?", (int(execution["case_id"]),)
    ).fetchone()
    owner = int(case_row["project_id"]) if case_row is not None and case_row["project_id"] else None
    if owner is None:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "无法确定用例所属项目，不能生成修复建议")
    if project_id is not None and int(project_id) != owner:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不属于该项目")

    variables = build_variables(conn, execution)
    run = generator or _default_generator(conn, owner, profile_id)
    try:
        outcome = run(variables)
    except AppError as exc:
        if exc.code == ErrorCode.AI_CREDENTIAL_MISSING:
            return {
                "generated": False,
                "message": _NO_MODEL_MESSAGE,
                "mechanicalDiff": _mechanical_diff(execution),
                "executionId": int(execution_id),
            }
        raise

    parsed = outcome.get("parsed")
    if not isinstance(parsed, dict):
        return {
            "generated": False,
            "message": "模型输出不是合法建议结构，请重试或人工比对",
            "mechanicalDiff": _mechanical_diff(execution),
            "executionId": int(execution_id),
            "raw": str(outcome.get("raw") or "")[:4000],
        }

    target = str(parsed.get("target") or TARGET_ASSERTION)
    if target not in TARGETS:
        target = TARGET_ASSERTION
    old_value = {
        TARGET_ASSERTION: variables["oldAssertions"],
        TARGET_EXPECTED_BODY: variables["oldExpectedBody"],
        TARGET_EXPECTED_STATUS: variables["oldExpectedStatus"],
    }.get(target, "")
    diff = _diff_preview(target, str(old_value or ""), _new_value_of(target, parsed))

    suggestion_no = next_no_monotonic(
        conn, "biz_fix_suggestion", "suggestion_no", SUGGESTION_PREFIX
    )
    now = utcnow_iso()
    cursor = conn.execute(
        """
        INSERT INTO biz_fix_suggestion
            (suggestion_no, project_id, execution_id, evidence_id, case_id, case_no,
             source, target_type, suggestion_json, diff_preview, status, model,
             usage_json, cost_json, degrade_json, created_at, updated_at, is_deleted)
        VALUES (?, ?, ?, ?, ?, ?, 'ai', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            suggestion_no,
            owner,
            int(execution_id),
            execution.get("evidence_id"),
            int(execution["case_id"]),
            variables["caseNo"],
            target,
            dumps(parsed),
            diff,
            STATUS_PENDING,
            str(outcome.get("model") or ""),
            dumps(outcome.get("usage") or {}),
            dumps(outcome.get("cost") or {}),
            dumps(outcome.get("degrade") or {}),
            now,
            now,
        ),
    )
    new_id = int(cursor.lastrowid or 0)
    audit_service.record(
        conn, audit_service.AUDIT_ACTION_GENERATE, RESOURCE,
        new_id, suggestion_no, {"executionId": int(execution_id), "target": target},
        actor=actor,
    )
    return {"generated": True, "suggestion": get_suggestion(conn, new_id, owner)}


def get_suggestion(
    conn: sqlite3.Connection, suggestion_id: int, project_id: int | None = None
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM biz_fix_suggestion WHERE id = ? AND is_deleted = 0",
        (int(suggestion_id),),
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "修复建议不存在")
    if project_id is not None and int(row["project_id"]) != int(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "修复建议不属于该项目")
    return _row_payload(row)


def list_suggestions(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    status: str | None = None,
    page_num: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    where = ["is_deleted = 0", "project_id = ?"]
    params: list[Any] = [int(project_id)]
    if status:
        if status not in STATUSES:
            raise AppError(ErrorCode.PARAM_ERROR, "未知状态")
        where.append("status = ?")
        params.append(status)
    clause = " AND ".join(where)
    total_row = conn.execute(
        f"SELECT COUNT(*) AS c FROM biz_fix_suggestion WHERE {clause}", params
    ).fetchone()
    total = int(total_row["c"]) if total_row is not None else 0
    page = max(1, int(page_num))
    size = max(1, min(200, int(page_size)))
    rows = conn.execute(
        f"SELECT * FROM biz_fix_suggestion WHERE {clause}"
        " ORDER BY id DESC LIMIT ? OFFSET ?",
        [*params, size, (page - 1) * size],
    ).fetchall()
    counts = {key: 0 for key in STATUSES}
    for item in conn.execute(
        "SELECT status, COUNT(*) AS c FROM biz_fix_suggestion"
        " WHERE is_deleted = 0 AND project_id = ? GROUP BY status",
        (int(project_id),),
    ).fetchall():
        key = str(item["status"] or "")
        if key in counts:
            counts[key] = int(item["c"])
    return {
        "list": [_row_payload(row) for row in rows],
        "total": total,
        "pageNum": page,
        "pageSize": size,
        "counts": counts,
    }


def _require_pending(conn: sqlite3.Connection, suggestion_id: int,
                     project_id: int | None) -> dict[str, Any]:
    payload = get_suggestion(conn, suggestion_id, project_id)
    if payload["status"] != STATUS_PENDING:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"该建议已是「{payload['statusLabel']}」终态，不能再次处理",
        )
    return payload


def adopt(
    conn: sqlite3.Connection,
    suggestion_id: int,
    *,
    project_id: int | None = None,
    actor: str = "local",
) -> dict[str, Any]:
    """采纳建议：把新值写回用例 extra，并锁定为 applied 终态。"""
    payload = _require_pending(conn, suggestion_id, project_id)
    target = str(payload["targetType"])
    if target == TARGET_BUG:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            "该建议判定为接口缺陷，请到缺陷页手工建单；AI 不直接建缺陷",
        )
    new_value = _new_value_of(target, payload["suggestion"])
    if not new_value:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "建议里没有可写回的新值，请忽略后重新生成")

    case_id = int(payload["caseId"])
    case = TestCaseRepository(conn).get_detail(case_id)
    if case is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "目标用例不存在或已删除")
    if project_id is not None and int(case.get("project_id") or 0) != int(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "目标用例不属于该项目")

    extra = dict(case.get("extra") or {})
    key = {
        TARGET_ASSERTION: EXTRA_KEY_ASSERTIONS,
        TARGET_EXPECTED_BODY: EXTRA_KEY_EXPECTED_BODY,
        TARGET_EXPECTED_STATUS: EXTRA_KEY_EXPECTED_STATUS,
    }[target]
    before = str(extra.get(key) or "")
    extra[key] = new_value
    TestCaseRepository(conn).update(case_id, {"extra_json": dumps(extra)})

    now = utcnow_iso()
    conn.execute(
        "UPDATE biz_fix_suggestion SET status = ?, applied_at = ?, applied_by = ?,"
        " updated_at = ? WHERE id = ?",
        (STATUS_APPLIED, now, actor, now, int(suggestion_id)),
    )
    audit_service.record(
        conn, audit_service.AUDIT_ACTION_UPDATE, RESOURCE,
        int(suggestion_id), str(payload["suggestionNo"]),
        {"caseId": case_id, "field": key, "before": before[:1000], "after": new_value[:1000]},
        actor=actor,
    )
    return {
        "applied": True,
        "suggestion": get_suggestion(conn, suggestion_id, project_id),
        "caseId": case_id,
        "field": key,
    }


def dismiss(
    conn: sqlite3.Connection,
    suggestion_id: int,
    *,
    project_id: int | None = None,
    actor: str = "local",
) -> dict[str, Any]:
    payload = _require_pending(conn, suggestion_id, project_id)
    now = utcnow_iso()
    conn.execute(
        "UPDATE biz_fix_suggestion SET status = ?, dismissed_at = ?, updated_at = ?"
        " WHERE id = ?",
        (STATUS_DISMISSED, now, now, int(suggestion_id)),
    )
    audit_service.record(
        conn, audit_service.AUDIT_ACTION_UPDATE, RESOURCE,
        int(suggestion_id), str(payload["suggestionNo"]), {"status": STATUS_DISMISSED},
        actor=actor,
    )
    return {"dismissed": True, "suggestion": get_suggestion(conn, suggestion_id, project_id)}
