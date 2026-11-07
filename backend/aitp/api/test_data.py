"""测试数据生成 API（文件分支，全程本地，不经过大模型）。

    GET    /api/v1/test-data/formats            文件格式目录（含分类与构造方式）
    POST   /api/v1/test-data/files              批量生成文件，返回批次与明细
    GET    /api/v1/test-data/batches            批次列表
    GET    /api/v1/test-data/batches/<pk>       批次详情（含明细）
    GET    /api/v1/test-data/files/<pk>/download 单个文件下载
    GET    /api/v1/test-data/batches/<pk>/archive 整批打包 zip 下载
    DELETE /api/v1/test-data/batches/<pk>       删除批次（记录与文件一起清）
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response

from ..common.validators import require_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.project_repo import ProjectRepository
from ..services import audit_service
from ..services.test_data_formats import formats_payload
from ..services.test_data_service import (
    delete_batch,
    generate_files,
    get_batch,
    list_batches,
    read_archive,
    read_file,
)
from .helpers import (
    audit,
    body,
    check_ref_project,
    db,
    download_response,
    keyword_of,
    page_args,
    path_int,
    query_filters,
    scope_arg,
)

bp = Blueprint("test_data", __name__)

FILTERS = {"projectId": "project_id", "versionId": "version_id"}


def _project_scope(filters: dict[str, Any]) -> int | None:
    raw = filters.get("project_id")
    return require_int(raw, "projectId") if raw not in (None, "") else None


def _version_scope(filters: dict[str, Any]) -> int | None:
    raw = filters.get("version_id")
    return require_int(raw, "versionId") if raw not in (None, "") else None


def _guard_batch_scope(batch: dict[str, Any]) -> None:
    """批次详情/归档是整包文件，带 projectId 时必须同项目。"""
    project_id = scope_arg()
    if project_id is not None and int(batch.get("projectId") or -1) != project_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "批次不属于该项目")


@bp.get("/formats")
def list_formats() -> Response:
    """格式目录一次性给全：前端本地模糊匹配，几百项也不用来回问后端。"""
    return ok(formats_payload())


@bp.post("/files")
def create_files() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    if not ProjectRepository(conn).get(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    module_id = data.get("moduleId")
    version_id = data.get("versionId")
    module_id = (
        check_ref_project(conn, "biz_module", require_int(module_id, "moduleId"),
                          project_id, "功能点", "moduleId")
        if module_id not in (None, "") else None
    )
    version_id = (
        check_ref_project(conn, "biz_project_version", require_int(version_id, "versionId"),
                          project_id, "项目版本", "versionId")
        if version_id not in (None, "") else None
    )
    batch = generate_files(
        conn,
        project_id=project_id,
        module_id=module_id,
        version_id=version_id,
        ext=data.get("ext"),
        size_bytes=data.get("sizeBytes"),
        file_count=data.get("fileCount", 1),
        name_prefix=data.get("namePrefix"),
        name_suffix=data.get("nameSuffix"),
        token_mode=data.get("tokenMode"),
        remark=data.get("remark"),
    )
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_DATA,
          int(batch["id"]), str(batch["batchNo"]),
          {"ext": batch["ext"], "count": batch["createdFiles"], "bytes": batch["totalBytes"]})
    return ok(batch)


@bp.get("/batches")
def list_batch_rows() -> Response:
    conn = db()
    page_num, page_size = page_args()
    filters = query_filters(FILTERS)
    page = list_batches(
        conn,
        _project_scope(filters),
        page_num,
        page_size,
        keyword_of() or None,
        _version_scope(filters),
    )
    return ok(page.as_dict())


@bp.get("/batches/<pk>")
def get_batch_detail(pk: str) -> Response:
    conn = db()
    batch = get_batch(conn, path_int(pk))
    if not batch:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "批次不存在或已删除")
    _guard_batch_scope(batch)
    return ok(batch)


@bp.get("/files/<pk>/download")
def download_file(pk: str) -> Response:
    conn = db()
    content, name, media_type = read_file(conn, path_int(pk))
    return download_response(content, name, media_type)


@bp.get("/batches/<pk>/archive")
def download_archive(pk: str) -> Response:
    conn = db()
    batch_id = path_int(pk)
    batch = get_batch(conn, batch_id)
    if not batch:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "批次不存在或已删除")
    _guard_batch_scope(batch)
    content, name = read_archive(conn, batch_id)
    return download_response(content, name, "application/zip",
                             count=int(batch.get("createdFiles") or 0))


@bp.delete("/batches/<pk>")
def remove_batch(pk: str) -> Response:
    conn = db()
    batch_id = path_int(pk)
    batch = get_batch(conn, batch_id)
    if not batch:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "批次不存在或已删除")
    _guard_batch_scope(batch)
    delete_batch(conn, batch_id)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TEST_DATA,
          batch_id, str(batch["batchNo"]), {"files": batch["createdFiles"]})
    return ok({"id": batch_id, "deleted": True})
