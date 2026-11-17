"""报告取数范围：项目全量 / 测试轮次 / 执行矩阵 / 接口场景批次（P1-8）。

只负责把「来源」解析成与项目报告同口径的统计字典，并给出标题、统计区间与
一句话范围说明；报告章节拼装仍留在 reports API，避免两套报告文案。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..common.enums import BUG_SEVERITY, BUG_STATUS, CASE_PRIORITY, CASE_TYPE, CASE_STATUS
from ..errors import AppError, ErrorCode
from ..repositories.base import loads
from ..repositories.test_run_repo import (
    CASE_STATUS_OF,
    RUN_BLOCKED,
    RUN_FAILED,
    RUN_PASSED,
    RUN_SKIPPED,
)

SOURCE_RUN = "run"
SOURCE_MATRIX = "matrix"
SOURCE_SCENARIO = "scenario"
SOURCES = (SOURCE_RUN, SOURCE_MATRIX, SOURCE_SCENARIO)
SOURCE_LABELS = {SOURCE_RUN: "测试轮次", SOURCE_MATRIX: "执行矩阵", SOURCE_SCENARIO: "接口场景"}


def normalize(source_type: Any) -> str:
    value = str(source_type or "").strip().lower()
    if not value:
        return ""
    if value not in SOURCES:
        raise AppError(ErrorCode.PARAM_ERROR,
                       f"sourceType 只能是 {'/'.join(SOURCES)}，当前 {value}")
    return value


def resolve(
    conn: sqlite3.Connection, project_id: int, source_type: str, source_id: int,
    source_run_id: int | None = None,
) -> dict[str, Any]:
    """按来源算统计；来源不存在或不属于本项目时抛 404。"""
    if source_type == SOURCE_RUN:
        return _resolve_run(conn, project_id, int(source_id))
    if source_type == SOURCE_MATRIX:
        return _resolve_matrix(conn, project_id, int(source_id))
    if source_type == SOURCE_SCENARIO:
        return _resolve_scenario(conn, project_id, int(source_id), source_run_id)
    raise AppError(ErrorCode.PARAM_ERROR, "缺少有效来源类型")


def _resolve_run(conn: sqlite3.Connection, project_id: int, run_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM biz_test_run WHERE id = ? AND project_id = ? AND is_deleted = 0",
        (run_id, project_id),
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在或不属于当前项目")
    run = dict(row)
    case_status = _zero(CASE_STATUS)
    priority_counts = _zero(CASE_PRIORITY)
    type_counts = _zero(CASE_TYPE)
    bug_ids: set[int] = set()
    for item in conn.execute(
        "SELECT result, priority, case_type, bug_id, COUNT(*) AS c"
        " FROM biz_test_run_case WHERE run_id = ? AND is_deleted = 0"
        " GROUP BY result, priority, case_type, bug_id",
        (run_id,),
    ).fetchall():
        count = int(item["c"] or 0)
        case_status[CASE_STATUS_OF.get(str(item["result"] or ""), "not_executed")] += count
        priority = str(item["priority"] or "P2")
        priority_counts[priority if priority in priority_counts else "P2"] += count
        case_type = str(item["case_type"] or "functional")
        type_counts[case_type if case_type in type_counts else "functional"] += count
        if item["bug_id"]:
            bug_ids.add(int(item["bug_id"]))
    label = f"轮次「{str(run.get('name') or run_id)}」"
    detail = f"轮次状态 {str(run.get('status') or '-')}，环境 {str(run.get('env') or '-')}。"
    return _payload(
        SOURCE_RUN, run_id, label, "用例",
        _stats(conn, case_status, priority_counts, type_counts, bug_ids),
        _date(run.get("started_at") or run.get("created_at")),
        _date(run.get("finished_at") or run.get("started_at") or run.get("created_at")),
        detail,
    )


def _resolve_matrix(conn: sqlite3.Connection, project_id: int, matrix_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM biz_execution_matrix WHERE id = ? AND project_id = ? AND is_deleted = 0",
        (matrix_id, project_id),
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行矩阵不存在或不属于当前项目")
    matrix = dict(row)
    case_status = _zero(CASE_STATUS)
    priority_counts = _zero(CASE_PRIORITY)
    type_counts = _zero(CASE_TYPE)
    bug_ids: set[int] = set()
    result_map = {
        RUN_PASSED: "pass", RUN_FAILED: "fail", RUN_BLOCKED: "blocked", RUN_SKIPPED: "not_executed",
        "passed": "pass", "failed": "fail", "blocked": "blocked", "skipped": "not_executed",
    }
    for item in conn.execute(
        "SELECT r.result, r.bug_id, c.priority, c.case_type, COUNT(*) AS c"
        " FROM biz_execution_matrix_result r"
        " LEFT JOIN biz_test_case c ON c.id = r.case_id AND c.is_deleted = 0"
        " WHERE r.matrix_id = ? AND r.is_deleted = 0"
        " GROUP BY r.result, r.bug_id, c.priority, c.case_type",
        (matrix_id,),
    ).fetchall():
        count = int(item["c"] or 0)
        case_status[result_map.get(str(item["result"] or ""), "not_executed")] += count
        priority = str(item["priority"] or "P2")
        priority_counts[priority if priority in priority_counts else "P2"] += count
        case_type = str(item["case_type"] or "functional")
        type_counts[case_type if case_type in type_counts else "functional"] += count
        if item["bug_id"]:
            bug_ids.add(int(item["bug_id"]))
    label = f"执行矩阵 {str(matrix.get('matrix_no') or matrix_id)}「{str(matrix.get('name') or '')}」"
    detail = (f"矩阵状态 {str(matrix.get('status') or '-')}，"
              f"格子 {int(matrix.get('cell_done') or 0)}/{int(matrix.get('cell_total') or 0)} 完成。")
    return _payload(
        SOURCE_MATRIX, matrix_id, label, "矩阵结果",
        _stats(conn, case_status, priority_counts, type_counts, bug_ids),
        _date(matrix.get("created_at")), _date(matrix.get("updated_at") or matrix.get("created_at")),
        detail,
    )


def _resolve_scenario(
    conn: sqlite3.Connection, project_id: int, scenario_id: int, run_id: int | None,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM biz_api_scenario WHERE id = ? AND project_id = ? AND is_deleted = 0",
        (scenario_id, project_id),
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "接口场景不存在或不属于当前项目")
    scenario = dict(row)
    sql = ("SELECT * FROM biz_api_scenario_run WHERE scenario_id = ? AND project_id = ?"
           " AND is_deleted = 0")
    params: list[Any] = [scenario_id, project_id]
    if run_id:
        sql += " AND id = ?"
        params.append(int(run_id))
    sql += " ORDER BY id ASC"
    runs = [dict(item) for item in conn.execute(sql, params).fetchall()]
    if run_id and not runs:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "场景执行记录不存在")

    case_status = _zero(CASE_STATUS)
    bug_ids: set[int] = set()
    record_total = 0
    for run in runs:
        summary = loads(run.get("summary_json"), {}) or {}
        records = summary.get("runs") if isinstance(summary, dict) else None
        if isinstance(records, list) and records:
            for record in records:
                if not isinstance(record, dict):
                    continue
                record_total += 1
                case_status["pass" if record.get("passed") else "fail"] += 1
        else:
            count = max(1, int(run.get("record_count") or 1))
            record_total += count
            case_status["pass" if int(run.get("passed") or 0) else "fail"] += count
        auto_bug_id = summary.get("autoBugId") if isinstance(summary, dict) else None
        if auto_bug_id:
            bug_ids.add(int(auto_bug_id))

    priority_counts = _zero(CASE_PRIORITY)
    type_counts = _zero(CASE_TYPE)
    total = sum(case_status.values())
    priority_counts["P2"] = total
    type_counts["interface"] = total
    if run_id and runs:
        run = runs[0]
        label = f"接口场景 {str(scenario.get('scenario_no') or '')}「{str(scenario.get('name') or '')}」执行 {run.get('run_no')}"
        detail = f"本次执行 {int(run.get('record_count') or 1)} 条数据记录，耗时 {int(run.get('duration_ms') or 0)} ms。"
        start = end = _date(run.get("created_at"))
    else:
        label = (f"接口场景 {str(scenario.get('scenario_no') or '')}「{str(scenario.get('name') or '')}」"
                 f"近 {len(runs)} 次执行")
        detail = f"共 {len(runs)} 次执行、{record_total} 条数据记录。"
        dates = [_date(run.get("created_at")) for run in runs if run.get("created_at")]
        start = min(dates) if dates else ""
        end = max(dates) if dates else ""
    return _payload(
        SOURCE_SCENARIO, scenario_id, label, "场景执行记录",
        _stats(conn, case_status, priority_counts, type_counts, bug_ids),
        start, end, detail,
    )


def _payload(
    source_type: str, source_id: int, label: str, unit: str,
    stats: dict[str, Any], period_start: str, period_end: str, detail: str,
) -> dict[str, Any]:
    return {
        "sourceType": source_type,
        "sourceId": int(source_id),
        "sourceLabel": label,
        "unit": unit,
        "detail": detail,
        "periodStart": period_start,
        "periodEnd": period_end,
        "stats": stats,
    }


def _zero(keys: tuple[str, ...]) -> dict[str, int]:
    return {key: 0 for key in keys}


def _date(value: Any) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else ""


def _stats(
    conn: sqlite3.Connection, case_status: dict[str, int],
    priority_counts: dict[str, int], type_counts: dict[str, int], bug_ids: set[int],
) -> dict[str, Any]:
    bug_status = _zero(BUG_STATUS)
    bug_severity = _zero(BUG_SEVERITY)
    if bug_ids:
        placeholders = ",".join("?" for _ in bug_ids)
        for item in conn.execute(
            f"SELECT status, severity, COUNT(*) AS c FROM biz_bug"
            f" WHERE is_deleted = 0 AND id IN ({placeholders})"
            " GROUP BY status, severity",
            tuple(sorted(bug_ids)),
        ).fetchall():
            count = int(item["c"] or 0)
            status = str(item["status"] or "")
            severity = str(item["severity"] or "")
            if status in bug_status:
                bug_status[status] += count
            if severity in bug_severity:
                bug_severity[severity] += count

    case_total = sum(case_status.values())
    bug_total = sum(bug_status.values())
    passed = case_status.get("pass", 0)
    open_bugs = sum(count for status, count in bug_status.items() if status != "closed")
    return {
        "caseStatus": case_status,
        "casePriority": priority_counts,
        "caseType": type_counts,
        "bugStatus": bug_status,
        "bugSeverity": bug_severity,
        "metrics": {
            "caseTotal": case_total,
            "passed": passed,
            "failed": case_status.get("fail", 0),
            "blocked": case_status.get("blocked", 0),
            "notExecuted": case_status.get("not_executed", 0),
            "passRate": round(passed / case_total, 4) if case_total else 0.0,
            "bugTotal": bug_total,
            "openBugs": open_bugs,
        },
    }
