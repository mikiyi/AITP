"""工作台首页概览。"""

from __future__ import annotations

import sqlite3
from typing import Any

from flask import Blueprint, Response, request

from ..ai.capability_codes import CAPABILITIES
from ..common.enums import BUG_STATUS, CASE_STATUS
from ..envelope import ok
from ..repositories.ai_session_repo import AITaskRepository
from ..repositories.bug_repo import BugRepository
from ..repositories.case_execution_repo import CaseExecutionRepository
from ..repositories.knowledge_draft_repo import KnowledgeDraftRepository
from ..repositories.knowledge_repo import KnowledgeRepository
from ..repositories.module_repo import ModuleRepository
from ..repositories.project_repo import ProjectRepository
from ..repositories.report_repo import ReportRepository
from ..repositories.requirement_repo import RequirementRepository
from ..repositories.test_case_repo import TestCaseRepository
from .helpers import db

bp = Blueprint("workbench", __name__)


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _shortcuts() -> list[dict[str, Any]]:
    items = [
        {"key": "modules", "label": "模块树", "path": "/settings/modules", "group": "结构"},
        {"key": "cases", "label": "测试用例", "path": "/test-cases", "group": "资产"},
        {"key": "bugs", "label": "缺陷", "path": "/bugs", "group": "资产"},
        {"key": "knowledge", "label": "知识库", "path": "/knowledge", "group": "资产"},
        {"key": "reports", "label": "测试报告", "path": "/reports", "group": "资产"},
        {"key": "requirements", "label": "需求", "path": "/requirements", "group": "资产"},
        {"key": "settings", "label": "系统设置", "path": "/settings", "group": "系统"},
    ]
    for code, spec in CAPABILITIES.items():
        items.append({
            "key": code,
            "label": spec.label,
            "path": f"/lab/{code}",
            "group": "AI 能力",
        })
    return items


@bp.get("/overview")
def overview() -> Response:
    conn = db()
    project_id = request.args.get("projectId")
    scope_sql = " AND project_id = ?" if project_id else ""
    scope_params: tuple[Any, ...] = (int(project_id),) if project_id else ()

    case_total = _count(conn, f"SELECT COUNT(*) FROM biz_test_case WHERE is_deleted = 0{scope_sql}", scope_params)
    case_pass = _count(
        conn,
        f"SELECT COUNT(*) FROM biz_test_case WHERE is_deleted = 0 AND status = 'pass'{scope_sql}",
        scope_params,
    )
    bug_total = _count(conn, f"SELECT COUNT(*) FROM biz_bug WHERE is_deleted = 0{scope_sql}", scope_params)
    bug_open = BugRepository(conn).open_bug_count(int(project_id) if project_id else None)
    requirement_total = _count(
        conn, f"SELECT COUNT(*) FROM biz_requirement WHERE is_deleted = 0{scope_sql}", scope_params
    )
    report_total = _count(conn, f"SELECT COUNT(*) FROM biz_report WHERE is_deleted = 0{scope_sql}", scope_params)
    task_total = _count(conn, "SELECT COUNT(*) FROM biz_ai_task WHERE is_deleted = 0")
    task_success = _count(
        conn, "SELECT COUNT(*) FROM biz_ai_task WHERE is_deleted = 0 AND status = 'success'"
    )
    module_total = _count(conn, "SELECT COUNT(*) FROM biz_module WHERE is_deleted = 0")

    knowledge_repo = KnowledgeRepository(conn)
    draft_repo = KnowledgeDraftRepository(conn)

    case_status = {status: 0 for status in CASE_STATUS}
    for row in conn.execute(
        f"SELECT status, COUNT(*) AS c FROM biz_test_case WHERE is_deleted = 0{scope_sql} GROUP BY status",
        scope_params,
    ).fetchall():
        case_status[row["status"]] = int(row["c"])

    bug_counts = BugRepository(conn).status_counts(int(project_id) if project_id else None)
    bug_status = {status: bug_counts.get(status, 0) for status in BUG_STATUS}

    recent_tasks = [
        {
            "id": row["id"],
            "taskType": row["task_type"],
            "capability": row.get("capability"),
            "status": row["status"],
            "moduleId": row.get("module_id"),
            "createdAt": row["created_at"],
            "finishedAt": row.get("finished_at"),
        }
        for row in AITaskRepository(conn).list_all(None, None, limit=5)
    ]

    return ok({
        "metrics": {
            "caseTotal": case_total,
            "casePassRate": round(case_pass / case_total, 4) if case_total else 0.0,
            "bugTotal": bug_total,
            "bugOpen": bug_open,
            "requirementTotal": requirement_total,
            "knowledgeTotal": knowledge_repo.published_count(),
            "reportTotal": report_total,
            "moduleTotal": module_total,
            "aiTaskTotal": task_total,
            "aiTaskSuccessRate": round(task_success / task_total, 4) if task_total else 0.0,
        },
        "assets": {
            "project": ProjectRepository(conn).count(),
            "requirement": requirement_total,
            "case": case_total,
            "bug": bug_total,
            "knowledge": knowledge_repo.published_count(),
            "report": report_total,
            "task": task_total,
        },
        "pendingKnowledge": draft_repo.pending_count(),
        "recentTasks": recent_tasks,
        "shortcuts": _shortcuts(),
        "caseStatus": case_status,
        "bugStatus": bug_status,
    })


@bp.get("/coverage")
def coverage() -> Response:
    conn = db()
    project_id = request.args.get("projectId")
    if not project_id:
        return ok({"list": []})
    return ok(TestCaseRepository(conn).coverage_matrix(int(project_id)))


@bp.get("/executions")
def execution_summary() -> Response:
    conn = db()
    project_id = request.args.get("projectId")
    pid = int(project_id) if project_id else None
    return ok(CaseExecutionRepository(conn).result_counts(pid))
