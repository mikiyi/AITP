"""案例验证沙箱 API。

验证案例是行为回归夹具：给输入、写下期望，跑沙箱看实际与期望差在哪。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.validators import optional_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.validation_case_repo import (
    ValidationCaseRepository,
    case_summary,
)
from ..services import audit_service, validation_jobs, validation_service
from .helpers import audit, body, commit, db, page_args, path_int

bp = Blueprint("validation", __name__)


@bp.get("/cases")
def list_cases() -> Response:
    conn = db()
    repo = ValidationCaseRepository(conn)
    filters: dict[str, Any] = {}
    status = request.args.get("status")
    if status:
        filters["status"] = status
    keyword = str(request.args.get("keyword") or "").strip()
    page_num, page_size = page_args()
    page = repo.list_detail(filters, keyword or None, page_num=page_num, page_size=page_size)
    return ok({
        "list": [case_summary(row) for row in page.list],
        "total": page.total,
        "pageNum": page_num,
        "pageSize": page_size,
    })


@bp.get("/cases/<pk>")
def get_case(pk: str) -> Response:
    conn = db()
    detail = ValidationCaseRepository(conn).get_detail(path_int(pk))
    if not detail:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "验证案例不存在")
    return ok(case_summary(detail))


@bp.post("/cases")
def create_case() -> Response:
    conn = db()
    data = body()
    new_id = validation_service.create_case(
        conn,
        name=require_str(data.get("name"), "name", 100),
        input_text=require_str(data.get("inputText"), "inputText", 8000),
        description=str(data.get("description") or ""),
        capability=str(data.get("capability") or ""),
        variables=data.get("variables") or {},
        expected_routes=data.get("expectedRoutes") or [],
        expected_missing=data.get("expectedMissing") or [],
        expected_rules=data.get("expectedRules") or [],
        status=str(data.get("status") or "published"),
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_VALIDATION_CASE,
          new_id, str(data.get("name") or ""), {})
    detail = ValidationCaseRepository(conn).get_detail(new_id)
    return ok(case_summary(detail or {}))


@bp.put("/cases/<pk>")
def update_case(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    data = body()
    detail = validation_service.update_case(conn, target, data)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_VALIDATION_CASE,
          target, str(detail.get("name") or ""), {"fields": sorted(data.keys())})
    return ok(case_summary(detail))


@bp.delete("/cases/<pk>")
def delete_case(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = ValidationCaseRepository(conn)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "验证案例不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_VALIDATION_CASE,
          target, str(row.get("name") or ""))
    return ok({"id": target, "deleted": True})


@bp.post("/cases/<pk>/run")
def run_case(pk: str) -> Response:
    """跑一个案例并回写比对结果。"""
    conn = db()
    data = body()
    project_id = optional_int(data.get("projectId"), "projectId")
    profile_id = optional_int(data.get("profileId"), "profileId")
    target = path_int(pk)
    result = validation_service.run_case(
        conn,
        target,
        project_id=project_id,
        profile_id=profile_id,
        run_capability=bool(data.get("runCapability", True)),
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_GENERATE, audit_service.RESOURCE_VALIDATION_CASE,
          target, "", {"passed": result.get("passed"), "errors": result.get("errors")})
    return ok(result)


@bp.post("/run-all")
def run_all() -> Response:
    """提交全量验证任务并立即返回，结果到任务详情（`GET /api/v1/tasks/<taskId>`）里取。

    逐条案例可能触发真实模型调用，整批耗时不可控，因此不在请求线程里跑（GAP-26）。
    返回体同时给出 `taskId` 与任务快照，汇总口径在 `result` 里：
    `{total, passed, failed, passRate, results}`。
    """
    conn = db()
    data = body()
    project_id = optional_int(data.get("projectId"), "projectId")
    profile_id = optional_int(data.get("profileId"), "profileId")
    task = validation_jobs.submit_run_all(conn, project_id=project_id, profile_id=profile_id)
    return ok({"queued": True, "taskId": task.get("id"), "task": task})
