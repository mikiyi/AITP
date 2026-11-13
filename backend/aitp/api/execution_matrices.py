"""执行矩阵 API（三期 GAP-43）。

矩阵定义 → 一次调度 → 逐格真实执行 → 跨格对比与差异归因 → 普遍失败一键转缺陷。
生产环境执行必须显式确认（confirmProd=true，默认不勾选）。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.validators import check_choice, optional_str, require_int
from ..envelope import ok
from ..repositories.execution_matrix_repo import ExecutionMatrixRepository
from ..services import audit_service, execution_matrix_service
from .helpers import (
    audit,
    body,
    commit,
    db,
    guard_project_scope,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    require_project,
    scope_arg,
)

bp = Blueprint("execution_matrices", __name__)

_AUDIT_RESOURCE = "execution_matrix"


@bp.get("")
def list_matrices() -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    require_project(conn, project_id)
    page_num, page_size = page_args()
    filters: dict[str, Any] = {"project_id": project_id}
    status = request.args.get("status")
    if status:
        filters["status"] = check_choice(
            status, execution_matrix_service.MATRIX_STATUSES, "status"
        )
    page = ExecutionMatrixRepository(conn).list(
        filters, keyword_of() or None, page_num=page_num, page_size=page_size
    )
    payload = page_payload(page)
    payload["list"] = [execution_matrix_service.matrix_summary(conn, row) for row in payload["list"]]
    return ok(payload)


@bp.post("")
def create_matrix() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    require_project(conn, project_id)
    detail = execution_matrix_service.create_matrix(conn, project_id, data)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, _AUDIT_RESOURCE,
          int(detail["matrix"]["id"]), str(detail["matrix"].get("name") or ""),
          {"matrixNo": detail["matrix"].get("matrixNo"), "mode": detail["matrix"].get("mode"),
           "cellTotal": detail["matrix"].get("cellTotal")})
    return ok(detail)


@bp.get("/<pk>")
def get_matrix(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = execution_matrix_service.get_matrix(conn, target)
    guard_project_scope(row, scope_arg())
    return ok(execution_matrix_service.get_matrix_detail(conn, target))


@bp.delete("/<pk>")
def delete_matrix(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = execution_matrix_service.get_matrix(conn, target)
    guard_project_scope(row, scope_arg())
    payload = execution_matrix_service.delete_matrix(conn, target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, _AUDIT_RESOURCE, target,
          str(row.get("name") or ""), {"matrixNo": row.get("matrix_no")})
    return ok(payload)


@bp.post("/<pk>/run")
def run_matrix(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = execution_matrix_service.get_matrix(conn, target)
    guard_project_scope(row, scope_arg())
    data = body()
    payload = execution_matrix_service.run_matrix(
        conn, target,
        confirm_prod=bool(data.get("confirmProd")),
        requested_by=optional_str(data.get("requestedBy"), "requestedBy", 64) or "local",
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, _AUDIT_RESOURCE, target,
          str(row.get("name") or ""), {
              "matrixNo": row.get("matrix_no"),
              "taskId": payload["task"].get("id"),
              "confirmProd": bool(data.get("confirmProd")),
              "prodEnvs": payload.get("prodEnvs"),
          })
    return ok(payload)


@bp.get("/<pk>/compare")
def compare_matrix(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = execution_matrix_service.get_matrix(conn, target)
    guard_project_scope(row, scope_arg())
    return ok(execution_matrix_service.compare(conn, target))


@bp.post("/<pk>/bugs")
def create_bugs(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = execution_matrix_service.get_matrix(conn, target)
    guard_project_scope(row, scope_arg())
    data = body()
    raw_nos = data.get("caseNos")
    case_nos = None
    if isinstance(raw_nos, list) and raw_nos:
        case_nos = [str(item) for item in raw_nos]
    payload = execution_matrix_service.create_bugs_from_matrix(
        conn, target, case_nos=case_nos, overrides=data,
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_BUG,
          int(payload["created"][0]["bugId"]) if payload["created"] else target,
          f"执行矩阵普遍失败转缺陷 · {row.get('matrix_no')}",
          {"matrixNo": row.get("matrix_no"), "count": payload["total"],
           "caseNos": [item.get("caseNo") for item in payload["created"]]})
    return ok(payload)
