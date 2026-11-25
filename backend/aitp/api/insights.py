"""质量洞察 API（V2.7.0）：失败聚类、用例依赖、变更影响面。"""

from __future__ import annotations

from flask import Blueprint, Response, request

from ..common.validators import optional_int, require_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import audit_service, insight_service
from .helpers import audit, body, commit, db, path_int

bp = Blueprint("insights", __name__)


@bp.get("/failure-clusters")
def get_failure_clusters() -> Response:
    conn = db()
    project_id = optional_int(request.args.get("projectId"), "projectId")
    try:
        rounds = int(request.args.get("rounds") or 5)
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.PARAM_ERROR, "rounds 必须是整数") from exc
    result = insight_service.failure_clusters(conn, project_id, rounds)
    result.pop("runOrder", None)
    return ok(result)


@bp.get("/dependencies")
def list_dependencies() -> Response:
    conn = db()
    project_id = optional_int(request.args.get("projectId"), "projectId")
    return ok(insight_service.list_dependencies(conn, project_id))


@bp.post("/dependencies")
def create_dependency() -> Response:
    conn = db()
    data = body()
    project_id = optional_int(data.get("projectId"), "projectId")
    case_id = require_int(data.get("caseId"), "caseId")
    depends_on = require_int(data.get("dependsOnCaseId"), "dependsOnCaseId")
    try:
        result = insight_service.add_dependency(
            conn, project_id, case_id, depends_on, str(data.get("note") or ""),
        )
    except ValueError as exc:
        raise AppError(ErrorCode.PARAM_ERROR, str(exc)) from exc
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_CASE, case_id,
          f"依赖用例 {depends_on}", result)
    return ok(result)


@bp.delete("/dependencies/<pk>")
def delete_dependency(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    try:
        insight_service.delete_dependency(conn, target)
    except ValueError as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, str(exc)) from exc
    commit(conn)
    return ok({"id": target, "deleted": True})


@bp.post("/impact")
def calc_impact() -> Response:
    conn = db()
    data = body()
    project_id = optional_int(data.get("projectId"), "projectId")
    raw_cases = data.get("caseIds") or []
    raw_requirements = data.get("requirementIds") or []
    if not isinstance(raw_cases, list) or not isinstance(raw_requirements, list):
        raise AppError(ErrorCode.PARAM_ERROR, "caseIds / requirementIds 必须是数组")
    try:
        case_ids = [int(item) for item in raw_cases]
        requirement_ids = [int(item) for item in raw_requirements]
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.PARAM_ERROR, "caseIds / requirementIds 必须全部是整数") from exc
    if not case_ids and not requirement_ids:
        raise AppError(ErrorCode.PARAM_ERROR, "请至少选择一个用例或需求")
    return ok(insight_service.impact(conn, project_id, case_ids, requirement_ids))
