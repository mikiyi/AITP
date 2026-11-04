"""缺陷 API：CRUD + 状态机流转 + 流转日志。"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.enums import BUG_RELATION, BUG_SEVERITY, BUG_STATUS, BUG_TYPE
from ..common.validators import check_choice, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.base import dumps, loads
from ..repositories.bug_repo import BugRepository
from ..repositories.test_case_repo import TestCaseRepository
from ..services import audit_service, tabular_export
from .helpers import (
    audit,
    batch_soft_delete,
    body,
    commit,
    db,
    download_response,
    ids_of,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
    tabular_format_of,
)

bp = Blueprint("bugs", __name__)

FILTERS = {
    "projectId": "project_id",
    "moduleId": "module_id",
    "status": "status",
    "severity": "severity",
    "bugType": "bug_type",
    "assignee": "assignee",
}
WRITE_FIELDS = {
    "title", "severity", "status", "priority", "bug_type", "description",
    "expected_result", "actual_result", "assignee", "reporter", "version", "env",
    "module", "module_id",
}


def _with_steps(row: dict[str, Any]) -> dict[str, Any]:
    """列表接口把 steps_json 还原成 steps。"""
    if "steps" in row:
        return row
    item = dict(row)
    item["steps"] = loads(item.pop("steps_json", None), []) or []
    return item


def _to_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "moduleId": row.get("module_id"),
        "bugNo": row.get("bug_no"),
        "title": row.get("title"),
        "severity": row.get("severity"),
        "status": row.get("status"),
        "priority": row.get("priority"),
        "bugType": row.get("bug_type"),
        "description": row.get("description"),
        "steps": row.get("steps") if "steps" in row else [],
        "expectedResult": row.get("expected_result"),
        "actualResult": row.get("actual_result"),
        "assignee": row.get("assignee"),
        "reporter": row.get("reporter"),
        "version": row.get("version"),
        "env": row.get("env"),
        "module": row.get("module"),
        "aiTaskId": row.get("ai_task_id"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


@bp.get("")
def list_bugs() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = BugRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [_to_public(_with_steps(row)) for row in payload["list"]]
    return ok(payload)


@bp.get("/stats")
def bug_stats() -> Response:
    conn = db()
    project_id = request.args.get("projectId")
    pid = require_int(project_id, "projectId") if project_id else None
    repo = BugRepository(conn)
    counts = repo.status_counts(pid)
    result = {status: counts.get(status, 0) for status in BUG_STATUS}
    return ok({
        "statusCounts": result,
        "openCount": repo.open_bug_count(pid),
        "total": sum(result.values()),
    })


BUG_TABULAR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("bugNo", "缺陷编号"),
    ("title", "标题"),
    ("severity", "严重级别"),
    ("priority", "优先级"),
    ("status", "状态"),
    ("bugType", "缺陷类型"),
    ("module", "所属模块"),
    ("assignee", "处理人"),
    ("reporter", "报告人"),
    ("version", "版本"),
    ("env", "环境"),
    ("description", "描述"),
    ("expectedResult", "预期结果"),
    ("actualResult", "实际结果"),
    ("createdAt", "创建时间"),
)


@bp.get("/export")
def export_bugs() -> Response:
    """导出缺陷清单为 CSV / JSON，筛选条件与列表接口一致。"""
    conn = db()
    fmt = tabular_format_of(request.args.get("format"))
    filters = query_filters(FILTERS)
    rows = BugRepository(conn).list_all(filters, keyword_of() or None, limit=10000)
    content, mimetype, extension = tabular_export.render(
        fmt, BUG_TABULAR_COLUMNS, [_to_public(row) for row in rows]
    )
    audit(conn, audit_service.AUDIT_ACTION_EXPORT, audit_service.RESOURCE_BUG, None, "",
          {"format": fmt, "count": len(rows)})
    return download_response(
        content, tabular_export.safe_filename("缺陷清单", extension), mimetype, count=len(rows)
    )


@bp.get("/<pk>")
def get_bug(pk: str) -> Response:
    conn = db()
    repo = BugRepository(conn)
    row = repo.get_detail(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "缺陷不存在")
    payload = _to_public(row)
    payload["linkedCaseIds"] = repo.linked_case_ids(path_int(pk))
    payload["logs"] = repo.list_logs(path_int(pk))
    return ok(payload)


@bp.post("")
def create_bug() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    title = require_str(data.get("title"), "title", 500)
    severity = check_choice(data.get("severity"), BUG_SEVERITY, "severity", "P2") or "P2"
    bug_type = check_choice(data.get("bugType"), BUG_TYPE, "bugType", "functional") or "functional"
    repo = BugRepository(conn)
    new_id = repo.insert({
        "project_id": project_id,
        "bug_no": repo.next_bug_no(project_id),
        "title": title,
        "severity": severity,
        "status": "open",
        "priority": optional_str(data.get("priority"), "priority", 20) or severity,
        "bug_type": bug_type,
        "description": optional_str(data.get("description"), "description", 100000),
        "steps_json": dumps(data.get("steps") or []),
        "expected_result": optional_str(data.get("expectedResult"), "expectedResult", 10000),
        "actual_result": optional_str(data.get("actualResult"), "actualResult", 10000),
        "assignee": optional_str(data.get("assignee"), "assignee", 64),
        "reporter": optional_str(data.get("reporter"), "reporter", 64),
        "version": optional_str(data.get("version"), "version", 64),
        "env": optional_str(data.get("env"), "env", 200),
        "module": optional_str(data.get("module"), "module", 200),
        "module_id": data.get("moduleId"),
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_BUG, new_id, title,
          {"severity": severity})
    return ok(_to_public(BugRepository(conn).get_detail(new_id) or {}))


@bp.put("/<pk>")
def update_bug(pk: str) -> Response:
    conn = db()
    repo = BugRepository(conn)
    target = path_int(pk)
    row = repo.get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "缺陷不存在")
    data = body()
    payload = {k: v for k, v in data.items() if k in WRITE_FIELDS}
    if "severity" in payload:
        payload["severity"] = check_choice(payload["severity"], BUG_SEVERITY, "severity", row["severity"])
    if "bug_type" in payload:
        payload["bug_type"] = check_choice(payload["bug_type"], BUG_TYPE, "bug_type", row["bug_type"])
    if "steps" in data:
        payload["steps_json"] = dumps(data.get("steps") or [])
    repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_BUG, target,
          str(payload.get("title", row.get("title", "")) or ""), {"fields": sorted(payload.keys())})
    return ok(_to_public(repo.get_detail(target) or {}))


@bp.delete("/<pk>")
def delete_bug(pk: str) -> Response:
    conn = db()
    repo = BugRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "缺陷不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_BUG, target,
          row.get("title", ""))
    return ok({"id": target, "deleted": True})


@bp.post("/batch-delete")
def batch_delete_bugs() -> Response:
    """批量删除缺陷；已删除或不存在的 id 归入 skipped。"""
    conn = db()
    ids = ids_of(body())
    return ok(batch_soft_delete(conn, BugRepository(conn), ids, audit_service.RESOURCE_BUG))


@bp.post("/<pk>/transition")
def transition_bug(pk: str) -> Response:
    conn = db()
    repo = BugRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "缺陷不存在")
    data = body()
    to_status = check_choice(data.get("toStatus"), BUG_STATUS, "toStatus")
    if not to_status:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 toStatus 不能为空")
    operator = optional_str(data.get("operator"), "operator", 64)
    comment = optional_str(data.get("comment"), "comment", 2000)
    result = repo.transition(target, to_status, operator=operator, comment=comment)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_TRANSITION, audit_service.RESOURCE_BUG, target,
          row.get("title", ""), {"from": result["fromStatus"], "to": result["toStatus"]})
    return ok(result)


@bp.get("/<pk>/logs")
def bug_logs(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    if not BugRepository(conn).get(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "缺陷不存在")
    return ok(BugRepository(conn).list_logs(target))


@bp.post("/<pk>/cases")
def link_bug_cases(pk: str) -> Response:
    conn = db()
    repo = BugRepository(conn)
    target = path_int(pk)
    if not repo.get(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "缺陷不存在")
    data = body()
    case_ids = data.get("caseIds")
    if not isinstance(case_ids, list):
        raise AppError(ErrorCode.PARAM_ERROR, "caseIds 必须是数组")
    ids = [require_int(c, "caseIds[]") for c in case_ids]
    relation = check_choice(data.get("relationType"), BUG_RELATION, "relationType", "related") or "related"
    missing = sorted(set(ids) - TestCaseRepository(conn).existing_ids(ids))
    if missing:
        raise AppError(
            ErrorCode.RESOURCE_NOT_FOUND,
            f"用例不存在：{ '、'.join(str(i) for i in missing) }",
        )
    linked = repo.link_cases(target, ids, relation)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_BUG, target, "",
          {"linkedCases": linked})
    return ok({"bugId": target, "linked": linked, "caseIds": repo.linked_case_ids(target)})


@bp.delete("/<pk>/cases")
def unlink_bug_cases(pk: str) -> Response:
    conn = db()
    repo = BugRepository(conn)
    target = path_int(pk)
    if not repo.get(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "缺陷不存在")
    data = body()
    case_ids = data.get("caseIds")
    if not isinstance(case_ids, list):
        raise AppError(ErrorCode.PARAM_ERROR, "caseIds 必须是数组")
    ids = [require_int(c, "caseIds[]") for c in case_ids]
    removed = repo.unlink_cases(target, ids)
    commit(conn)
    return ok({"bugId": target, "removed": removed, "caseIds": repo.linked_case_ids(target)})
