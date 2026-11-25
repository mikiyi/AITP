"""业务对象持久附件 API：列表 / 上传 / 预览下载 / 删除。

挂在缺陷、用例、轮次、探索会话等业务对象下，和 AI 入口的临时附件是两套存储。
归属对象与项目归属都在服务层校验，跨项目一律按「不存在」处理。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from flask import Blueprint, Response, request

from ..common.validators import require_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import audit_service, business_attachment_service
from .helpers import audit, commit, db, path_int, scope_arg

bp = Blueprint("attachments", __name__)


def _owner_args(source: Any) -> tuple[str, int, int]:
    owner_type = str(source.get("ownerType") or "").strip()
    owner_id = require_int(source.get("ownerId"), "ownerId")
    project_id = require_int(source.get("projectId"), "projectId")
    return owner_type, owner_id, project_id


@bp.get("")
def list_attachments() -> Response:
    owner_type, owner_id, project_id = _owner_args(request.args)
    conn = db()
    items = business_attachment_service.list_attachments(
        conn, owner_type, owner_id, project_id
    )
    return ok({"list": items, "total": len(items)})


@bp.post("")
def upload_attachments() -> Response:
    # ownerType / ownerId 既允许放在查询串，也允许跟文件一起放在 multipart 表单里
    owner_type, owner_id, project_id = _owner_args(request.values)
    uploads = [item for item in request.files.getlist("files") if item and item.filename]
    if not uploads:
        raise AppError(ErrorCode.PARAM_ERROR, "请选择要上传的附件")
    files = [
        (item.filename or "attachment", item.read(), item.mimetype or "")
        for item in uploads
    ]
    conn = db()
    result = business_attachment_service.save_attachments(
        conn, owner_type, owner_id, project_id, files
    )
    commit(conn)
    audit(
        conn,
        audit_service.AUDIT_ACTION_CREATE,
        audit_service.RESOURCE_ATTACHMENT,
        int(owner_id),
        business_attachment_service.OWNER_LABELS.get(owner_type, owner_type),
        {
            "ownerType": owner_type,
            "uploaded": [item["fileName"] for item in result["list"]],
            "skipped": [item["fileName"] for item in result["skipped"]],
        },
    )
    return ok(result)


@bp.get("/<pk>/file")
def get_attachment_file(pk: str) -> Response:
    conn = db()
    row = business_attachment_service.get_attachment(conn, path_int(pk), scope_arg())
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "附件不存在或已删除")
    path = business_attachment_service.file_path(conn, row)
    if not path.is_file():
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "附件文件已丢失")
    mime = str(row.get("mime") or "application/octet-stream")
    previewable = str(row.get("kind") or "file") == "image" and mime in {
        "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
    }
    filename = quote(str(row.get("original_name") or row.get("file_name") or ""), safe="")
    response = Response(path.read_bytes(), mimetype=mime)
    disposition = "inline" if previewable else "attachment"
    response.headers["Content-Disposition"] = f"{disposition}; filename*=UTF-8''{filename}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    if not previewable:
        # 非预览件一律以下载方式回给浏览器；SVG/HTML 这类可执行格式因此不会被同源执行
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@bp.delete("/<pk>")
def remove_attachment(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = business_attachment_service.get_attachment(conn, target, scope_arg())
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "附件不存在或已删除")
    business_attachment_service.delete_attachment(conn, row)
    commit(conn)
    audit(
        conn,
        audit_service.AUDIT_ACTION_DELETE,
        audit_service.RESOURCE_ATTACHMENT,
        target,
        str(row.get("original_name") or ""),
        {"ownerType": row.get("owner_type"), "ownerId": row.get("owner_id")},
    )
    return ok({"id": target, "deleted": True})
