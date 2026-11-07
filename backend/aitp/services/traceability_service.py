"""追溯矩阵：需求 → 用例 → 执行结果 → 缺陷。

矩阵不是快照表，而是按当前项目/版本/轮次实时汇总；
口径与用例列表、缺陷列表、测试轮次保持一致，避免三处各算一套。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..common.enums import BUG_OPEN_STATUSES

COVERAGE_UNCOVERED = "uncovered"
COVERAGE_NOT_EXECUTED = "not_executed"
COVERAGE_IN_PROGRESS = "in_progress"
COVERAGE_FAILED = "failed"
COVERAGE_PASSED = "passed"

COVERAGE_LABELS = {
    COVERAGE_UNCOVERED: "未覆盖",
    COVERAGE_NOT_EXECUTED: "未执行",
    COVERAGE_IN_PROGRESS: "执行中",
    COVERAGE_FAILED: "有未通过",
    COVERAGE_PASSED: "全部通过",
}


def build_traceability(
    conn: sqlite3.Connection,
    project_id: int,
    version_id: int | None = None,
    run_id: int | None = None,
) -> dict[str, Any]:
    req_sql = "SELECT id, req_no, title, status, priority FROM biz_requirement WHERE project_id = ? AND is_deleted = 0"
    req_params: list[Any] = [project_id]
    if version_id is not None:
        req_sql += " AND version_id = ?"
        req_params.append(version_id)
    req_sql += " ORDER BY req_no, id"
    requirements = [dict(row) for row in conn.execute(req_sql, req_params).fetchall()]

    if run_id is not None:
        case_sql = (
            "SELECT c.requirement_id AS req_id, COUNT(*) AS case_count,"
            " SUM(CASE WHEN rc.result = 'passed' THEN 1 ELSE 0 END) AS passed,"
            " SUM(CASE WHEN rc.result = 'failed' THEN 1 ELSE 0 END) AS failed,"
            " SUM(CASE WHEN rc.result = 'blocked' THEN 1 ELSE 0 END) AS blocked,"
            " SUM(CASE WHEN rc.result = 'skipped' THEN 1 ELSE 0 END) AS skipped,"
            " SUM(CASE WHEN rc.result = 'not_executed' THEN 1 ELSE 0 END) AS not_executed,"
            " MAX(rc.executed_at) AS last_executed_at"
            " FROM biz_test_run_case rc"
            " JOIN biz_test_case c ON c.id = rc.case_id AND c.is_deleted = 0"
            " WHERE rc.run_id = ? AND rc.is_deleted = 0"
            " GROUP BY c.requirement_id"
        )
        case_params: list[Any] = [run_id]
    else:
        case_sql = (
            "SELECT c.requirement_id AS req_id, COUNT(*) AS case_count,"
            " SUM(CASE WHEN c.status = 'pass' THEN 1 ELSE 0 END) AS passed,"
            " SUM(CASE WHEN c.status = 'fail' THEN 1 ELSE 0 END) AS failed,"
            " SUM(CASE WHEN c.status = 'blocked' THEN 1 ELSE 0 END) AS blocked,"
            " 0 AS skipped,"
            " SUM(CASE WHEN c.status = 'not_executed' THEN 1 ELSE 0 END) AS not_executed,"
            " MAX(e.last_at) AS last_executed_at"
            " FROM biz_test_case c"
            " LEFT JOIN (SELECT case_id, MAX(executed_at) AS last_at FROM biz_case_execution"
            "            WHERE is_deleted = 0 GROUP BY case_id) e ON e.case_id = c.id"
            " WHERE c.project_id = ? AND c.is_deleted = 0"
            " GROUP BY c.requirement_id"
        )
        case_params = [project_id]
    case_stats = {
        int(row["req_id"]): dict(row)
        for row in conn.execute(case_sql, case_params).fetchall()
        if row["req_id"] is not None
    }

    bug_placeholders = ",".join("?" for _ in BUG_OPEN_STATUSES)
    bug_sql = (
        "SELECT c.requirement_id AS req_id, COUNT(DISTINCT b.id) AS open_bugs"
        " FROM biz_case_bug_rel rel"
        " JOIN biz_test_case c ON c.id = rel.case_id AND c.is_deleted = 0"
        " JOIN biz_bug b ON b.id = rel.bug_id AND b.is_deleted = 0"
        f" AND b.status IN ({bug_placeholders})"
        " WHERE c.project_id = ?"
    )
    bug_params: list[Any] = list(BUG_OPEN_STATUSES) + [project_id]
    if version_id is not None:
        bug_sql += " AND c.version_id = ?"
        bug_params.append(version_id)
    bug_sql += " GROUP BY c.requirement_id"
    open_bugs = {
        int(row["req_id"]): int(row["open_bugs"])
        for row in conn.execute(bug_sql, bug_params).fetchall()
        if row["req_id"] is not None
    }

    rows: list[dict[str, Any]] = []
    for req in requirements:
        req_id = int(req["id"])
        stat = case_stats.get(req_id, {})
        case_count = int(stat.get("case_count") or 0)
        passed = int(stat.get("passed") or 0)
        failed = int(stat.get("failed") or 0)
        blocked = int(stat.get("blocked") or 0)
        skipped = int(stat.get("skipped") or 0)
        not_executed = int(stat.get("not_executed") or 0)
        executed = passed + failed + blocked + skipped
        if case_count == 0:
            coverage = COVERAGE_UNCOVERED
        elif executed == 0:
            coverage = COVERAGE_NOT_EXECUTED
        elif failed > 0 or blocked > 0:
            # 阻塞和失败都不能算通过；跳过则留在「执行中」，避免全跳过被标成全部通过
            coverage = COVERAGE_FAILED
        elif passed == case_count:
            coverage = COVERAGE_PASSED
        else:
            coverage = COVERAGE_IN_PROGRESS
        rows.append({
            "requirementId": req_id,
            "reqNo": req.get("req_no"),
            "title": req.get("title"),
            "requirementStatus": req.get("status"),
            "priority": req.get("priority"),
            "caseCount": case_count,
            "executedCount": executed,
            "passed": passed,
            "failed": failed,
            "blocked": blocked,
            "notExecuted": not_executed,
            "passRate": round(passed / executed, 4) if executed else 0.0,
            "openBugs": open_bugs.get(req_id, 0),
            "lastExecutedAt": stat.get("last_executed_at") or "",
            "coverageStatus": coverage,
            "coverageLabel": COVERAGE_LABELS[coverage],
        })

    total_cases = sum(row["caseCount"] for row in rows)
    total_executed = sum(row["executedCount"] for row in rows)
    total_passed = sum(row["passed"] for row in rows)
    covered = sum(1 for row in rows if row["caseCount"] > 0)
    summary = {
        "requirementTotal": len(rows),
        "coveredRequirements": covered,
        "uncoveredRequirements": len(rows) - covered,
        "caseCount": total_cases,
        "executedCount": total_executed,
        "passed": total_passed,
        "failed": sum(row["failed"] for row in rows),
        "openBugs": sum(row["openBugs"] for row in rows),
        "coverageRate": round(covered / len(rows) * 100, 2) if rows else 0.0,
        "passRate": round(total_passed / total_executed * 100, 2) if total_executed else 0.0,
    }
    return {
        "projectId": project_id,
        "versionId": version_id,
        "runId": run_id,
        "summary": summary,
        "list": rows,
    }
