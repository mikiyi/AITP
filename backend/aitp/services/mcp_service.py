"""AITP MCP 服务端工具层（V2.7.0）。

定位：把 AITP 里已有的项目、需求、用例、轮次、缺陷、洞察以**只读工具**暴露给
外部 AI 客户端（Trae / Claude Desktop 等），唯一写工具 ``bugs.draft.create``
只落「缺陷待审区」，不直接写正式缺陷表——人确认后才进入 ``biz_bug``。

协议适配（JSON-RPC 2.0）在 ``api/mcp.py``，本模块只负责工具定义与取数，
方便单测与确定性门禁直接调用。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from ..common.time_utils import utcnow_iso

MAX_ROWS = 50
MAX_TEXT = 4000


class McpError(Exception):
    """工具调用错误：由 API 层转成 JSON-RPC error / isError 内容。"""


def _text(value: Any, limit: int = 300) -> str:
    return str(value or "")[:limit]


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _limit(args: dict[str, Any], default: int = 20) -> int:
    try:
        value = int(args.get("limit") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_ROWS))


def _project_filter(args: dict[str, Any], alias: str = "") -> tuple[str, list[Any]]:
    project_id = args.get("projectId")
    if project_id in (None, ""):
        return "", []
    try:
        return f" AND {alias}project_id = ?", [int(str(project_id))]
    except (TypeError, ValueError) as exc:
        raise McpError("projectId 必须是整数") from exc


# --------------------------------------------------------------------------- 只读工具

def list_projects(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    rows = _rows(conn, """
        SELECT id, code, name, owner, status, updated_at AS updatedAt
        FROM biz_project WHERE is_deleted = 0 ORDER BY id DESC LIMIT ?
    """, (_limit(args, 50),))
    return {"total": len(rows), "items": rows}


def search_cases(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    where, params = _project_filter(args)
    keyword = str(args.get("keyword") or "").strip()
    if keyword:
        where += " AND (title LIKE ? OR case_no LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%"]
    rows = _rows(conn, f"""
        SELECT id, project_id AS projectId, case_no AS caseNo, title, case_type AS caseType,
               priority, status, module_id AS moduleId, requirement_id AS requirementId,
               updated_at AS updatedAt
        FROM biz_test_case WHERE is_deleted = 0{where}
        ORDER BY id DESC LIMIT ?
    """, (*params, _limit(args)))
    return {"total": len(rows), "items": rows}


def get_case(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    try:
        case_id = int(str(args.get("id")))
    except (TypeError, ValueError) as exc:
        raise McpError("id 必须是整数") from exc
    row = conn.execute("""
        SELECT id, project_id AS projectId, case_no AS caseNo, title, case_type AS caseType,
               priority, status, preconditions, steps_json AS stepsJson,
               expected_result AS expectedResult, actual_result AS actualResult,
               module_id AS moduleId, requirement_id AS requirementId, updated_at AS updatedAt
        FROM biz_test_case WHERE id = ? AND is_deleted = 0
    """, (case_id,)).fetchone()
    if row is None:
        raise McpError(f"用例 {case_id} 不存在")
    item = dict(row)
    try:
        item["steps"] = json.loads(str(item.pop("stepsJson") or "[]"))
    except (TypeError, ValueError):
        item["steps"] = []
    for key in ("preconditions", "expectedResult", "actualResult"):
        item[key] = _text(item.get(key), MAX_TEXT)
    return item


def search_requirements(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    where, params = _project_filter(args)
    keyword = str(args.get("keyword") or "").strip()
    if keyword:
        where += " AND (title LIKE ? OR req_no LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%"]
    rows = _rows(conn, f"""
        SELECT id, project_id AS projectId, req_no AS reqNo, title, status, priority,
               version, updated_at AS updatedAt
        FROM biz_requirement WHERE is_deleted = 0{where}
        ORDER BY id DESC LIMIT ?
    """, (*params, _limit(args)))
    return {"total": len(rows), "items": rows}


def recent_runs(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    where, params = _project_filter(args)
    rows = _rows(conn, f"""
        SELECT id, project_id AS projectId, name, status, total, passed, failed, blocked,
               not_executed AS notExecuted, started_at AS startedAt, finished_at AS finishedAt
        FROM biz_test_run WHERE is_deleted = 0{where}
        ORDER BY id DESC LIMIT ?
    """, (*params, _limit(args, 10)))
    return {"total": len(rows), "items": rows}


def run_summary(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    try:
        run_id = int(str(args.get("runId")))
    except (TypeError, ValueError) as exc:
        raise McpError("runId 必须是整数") from exc
    run = conn.execute("""
        SELECT id, project_id AS projectId, name, status, total, passed, failed, blocked,
               skipped, not_executed AS notExecuted, started_at AS startedAt,
               finished_at AS finishedAt
        FROM biz_test_run WHERE id = ? AND is_deleted = 0
    """, (run_id,)).fetchone()
    if run is None:
        raise McpError(f"轮次 {run_id} 不存在")
    failures = _rows(conn, """
        SELECT case_id AS caseId, case_no AS caseNo, title, result,
               actual_result AS actualResult, remark
        FROM biz_test_run_case
        WHERE run_id = ? AND is_deleted = 0 AND result IN ('failed', 'blocked')
        ORDER BY seq ASC LIMIT 20
    """, (run_id,))
    return {"run": dict(run), "failedCases": failures}


def search_bugs(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    where, params = _project_filter(args)
    status = str(args.get("status") or "").strip()
    if status:
        where += " AND status = ?"
        params.append(status)
    keyword = str(args.get("keyword") or "").strip()
    if keyword:
        where += " AND (title LIKE ? OR bug_no LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%"]
    rows = _rows(conn, f"""
        SELECT id, project_id AS projectId, bug_no AS bugNo, title, severity, status,
               priority, bug_type AS bugType, assignee, reporter,
               source, source_ref AS sourceRef, created_at AS createdAt
        FROM biz_bug WHERE is_deleted = 0{where}
        ORDER BY id DESC LIMIT ?
    """, (*params, _limit(args)))
    return {"total": len(rows), "items": rows}


def failure_clusters(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    from . import insight_service  # 局部导入，避免服务间循环依赖

    project_id = args.get("projectId")
    rounds = args.get("rounds") or 5
    try:
        return insight_service.failure_clusters(
            conn, int(project_id) if project_id not in (None, "") else None, int(rounds)
        )
    except (TypeError, ValueError) as exc:
        raise McpError("projectId / rounds 必须是整数") from exc


def case_impact(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    from . import insight_service

    raw_ids = args.get("caseIds") or []
    if not isinstance(raw_ids, list):
        raise McpError("caseIds 必须是数组")
    try:
        case_ids = [int(item) for item in raw_ids]
    except (TypeError, ValueError) as exc:
        raise McpError("caseIds 必须全部是整数") from exc
    project_id = args.get("projectId")
    return insight_service.impact(
        conn, project_id=int(project_id) if project_id not in (None, "") else None,
        case_ids=case_ids,
        requirement_ids=[int(item) for item in (args.get("requirementIds") or [])],
    )


# --------------------------------------------------------------------------- 唯一写工具

def create_bug_draft(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    """写操作只进待审区：外部 AI 不能直接创建正式缺陷。"""
    try:
        project_id = int(str(args.get("projectId")))
    except (TypeError, ValueError) as exc:
        raise McpError("projectId 必须是整数") from exc
    project = conn.execute(
        "SELECT name FROM biz_project WHERE id = ? AND is_deleted = 0", (project_id,)
    ).fetchone()
    if project is None:
        raise McpError(f"项目 {project_id} 不存在")
    title = str(args.get("title") or "").strip()
    if not title:
        raise McpError("title 不能为空")
    now = utcnow_iso()
    cursor = conn.execute("""
        INSERT INTO biz_bug_draft
            (project_id, source, source_ref, title, description, actual_result,
             severity, priority, bug_type, reporter, case_ids_json, status,
             created_at, updated_at, is_deleted)
        VALUES (?, 'mcp', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0)
    """, (
        project_id, _text(args.get("sourceRef"), 200), _text(title, 200),
        _text(args.get("description"), MAX_TEXT), _text(args.get("actualResult"), MAX_TEXT),
        _text(args.get("severity") or "P2", 20), _text(args.get("priority") or "P2", 20),
        _text(args.get("bugType") or "functional", 40),
        _text(args.get("reporter") or "MCP 客户端", 100),
        json.dumps(args.get("caseIds") or [], ensure_ascii=False), now, now,
    ))
    return {
        "draftId": int(cursor.lastrowid or 0),
        "status": "pending",
        "message": "已进入缺陷待审区，需在 AITP「缺陷管理」中人工确认后才会创建正式缺陷",
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "projects.list",
        "description": "列出 AITP 项目（只读）",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": list_projects,
    },
    {
        "name": "cases.search",
        "description": "按项目/关键字检索测试用例（只读）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer"},
                "keyword": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS},
            },
            "additionalProperties": False,
        },
        "handler": search_cases,
    },
    {
        "name": "cases.get",
        "description": "读取单条测试用例详情（只读）",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "handler": get_case,
    },
    {
        "name": "requirements.search",
        "description": "检索需求（只读）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer"},
                "keyword": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS},
            },
            "additionalProperties": False,
        },
        "handler": search_requirements,
    },
    {
        "name": "runs.recent",
        "description": "列出最近的测试轮次（只读）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS},
            },
            "additionalProperties": False,
        },
        "handler": recent_runs,
    },
    {
        "name": "runs.summary",
        "description": "读取轮次汇总与失败用例（只读）",
        "inputSchema": {
            "type": "object",
            "properties": {"runId": {"type": "integer"}},
            "required": ["runId"],
            "additionalProperties": False,
        },
        "handler": run_summary,
    },
    {
        "name": "bugs.search",
        "description": "检索正式缺陷（只读）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer"},
                "status": {"type": "string"},
                "keyword": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS},
            },
            "additionalProperties": False,
        },
        "handler": search_bugs,
    },
    {
        "name": "insights.failure_clusters",
        "description": "跨轮次失败聚类（只读）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer"},
                "rounds": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "additionalProperties": False,
        },
        "handler": failure_clusters,
    },
    {
        "name": "insights.case_impact",
        "description": "按用例/需求计算依赖影响面（只读）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer"},
                "caseIds": {"type": "array", "items": {"type": "integer"}},
                "requirementIds": {"type": "array", "items": {"type": "integer"}},
            },
            "additionalProperties": False,
        },
        "handler": case_impact,
    },
    {
        "name": "bugs.draft.create",
        "description": "提交缺陷草稿（写入待审区，人工确认后才创建正式缺陷）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "actualResult": {"type": "string"},
                "severity": {"type": "string"},
                "priority": {"type": "string"},
                "bugType": {"type": "string"},
                "reporter": {"type": "string"},
                "sourceRef": {"type": "string"},
                "caseIds": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["projectId", "title"],
            "additionalProperties": False,
        },
        "handler": create_bug_draft,
        "write": True,
    },
]

_BY_NAME: dict[str, dict[str, Any]] = {tool["name"]: tool for tool in TOOLS}


def tool_definitions() -> list[dict[str, Any]]:
    """返回 tools/list 需要的定义（剔除 handler / write 等内部字段）。"""
    return [
        {"name": tool["name"], "description": tool["description"],
         "inputSchema": tool["inputSchema"]}
        for tool in TOOLS
    ]


def is_write_tool(name: str) -> bool:
    return bool(_BY_NAME.get(name, {}).get("write"))


def call_tool(conn: Any, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    tool = _BY_NAME.get(name)
    if tool is None:
        raise McpError(f"未知工具：{name}")
    handler: Callable[[Any, dict[str, Any]], dict[str, Any]] = tool["handler"]
    return handler(conn, arguments if isinstance(arguments, dict) else {})


__all__ = [
    "McpError", "TOOLS", "call_tool", "create_bug_draft", "is_write_tool", "tool_definitions",
]
