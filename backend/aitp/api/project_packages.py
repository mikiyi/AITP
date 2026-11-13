"""项目级资产包 API（三期 GAP-41）。"""

from __future__ import annotations

from flask import Blueprint, Response, request

from ..common.validators import require_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.project_repo import ProjectRepository
from ..services import audit_service, project_package_service
from .helpers import audit, body, commit, db, download_response

bp = Blueprint("project_packages", __name__)

MAX_PACKAGE_BYTES = 50 * 1024 * 1024


def _uploaded_bytes() -> bytes:
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise AppError(ErrorCode.PARAM_ERROR, "请选择资产包 .zip 文件")
    data = uploaded.read()
    if len(data) > MAX_PACKAGE_BYTES:
        raise AppError(ErrorCode.PARAM_ERROR, "资产包不能超过 50 MB")
    return bytes(data)


@bp.post("/export")
def export_project_package() -> Response:
    conn = db()
    project_id = require_int(body().get("projectId"), "projectId")
    project = ProjectRepository(conn).get(project_id)
    if not project:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    content = project_package_service.export_package(conn, project_id)
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_PROJECT,
          project_id, str(project.get("code") or ""), {"kind": "package_export"})
    commit(conn)
    return download_response(
        content, f"{str(project.get('code') or 'project')}-package.zip", "application/zip")


@bp.post("/preview")
def preview_project_package() -> Response:
    conn = db()
    return ok(project_package_service.preview_package(conn, _uploaded_bytes()))


@bp.post("/import")
def import_project_package() -> Response:
    conn = db()
    conflict = str(request.form.get("conflict") or "skip").strip().lower()
    result = project_package_service.import_package(
        conn, _uploaded_bytes(), conflict=conflict)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_PROJECT,
          int(result["projectId"]), str(result["code"]),
          {"kind": "package_import", "counts": result["counts"], "conflict": conflict})
    commit(conn)
    return ok(result)
