"""测试文档模板 API。

    GET    /api/v1/doc-templates            列表（按类别过滤）
    GET    /api/v1/doc-templates/options    某类别可用模板（能力页下拉用）
    GET    /api/v1/doc-templates/<pk>       详情
    GET    /api/v1/doc-templates/<pk>/file  下载最近一次上传的模板原件
    POST   /api/v1/doc-templates/upload     上传模板文件，解析正文 / 列名 / 版式
    POST   /api/v1/doc-templates/uploads/cleanup 清理取消保存后遗留的上传原件
    POST   /api/v1/doc-templates            新增
    PUT    /api/v1/doc-templates/<pk>       修改
    DELETE /api/v1/doc-templates/<pk>       删除（软删）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, request, send_file

from ..common.validators import check_choice, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.doc_template_repo import (
    MAX_CONTENT,
    MAX_STYLE_HINT,
    TEMPLATE_KINDS,
    DocTemplateRepository,
    decorate,
    dump_columns,
    normalize_columns,
)
from ..services import audit_service, doc_template_service
from .helpers import audit, body, commit, db, keyword_of, page_args, page_payload, path_int

bp = Blueprint("doc_templates", __name__)

#: 允许写入的列（白名单）
_FIELDS = (
    "name", "kind", "description", "columns_json", "content", "style_hint",
    "file_name", "file_type", "file_size", "stored_name", "is_default", "updated_at",
)


def _templates_root() -> Path:
    """模板原文件与数据库同放在 data 目录下，不进 static（那是构建产物目录）。"""
    return Path(current_app.config["AITP_DB_PATH"]).parent / "doc_templates"


def _payload(data: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """把请求体规整成可落库的字段；partial 为真时只处理出现的字段。"""
    result: dict[str, Any] = {}
    if not partial or "name" in data:
        result["name"] = require_str(data.get("name"), "name", 100)
    if not partial or "kind" in data:
        kind = check_choice(str(data.get("kind") or "case"), TEMPLATE_KINDS, "kind", "case")
        result["kind"] = kind or "case"
    if not partial or "description" in data:
        result["description"] = optional_str(data.get("description"), "description", 500)
    if not partial or "content" in data:
        result["content"] = optional_str(data.get("content"), "content", MAX_CONTENT)
    if not partial or "columns" in data:
        result["columns_json"] = dump_columns(normalize_columns(data.get("columns")))
    if not partial or "styleHint" in data:
        result["style_hint"] = optional_str(data.get("styleHint"), "styleHint", MAX_STYLE_HINT)
    if "fileName" in data:
        result["file_name"] = optional_str(data.get("fileName"), "fileName", 120)
    if "fileType" in data:
        result["file_type"] = optional_str(data.get("fileType"), "fileType", 20)
    if "fileSize" in data:
        result["file_size"] = max(0, require_int(data.get("fileSize"), "fileSize", 0))
    if "storedName" in data:
        # 落盘名只认 upload 接口发回来的格式，非法就当没文件
        stored = optional_str(data.get("storedName"), "storedName", 60)
        result["stored_name"] = (
            stored if doc_template_service.is_valid_stored_name(stored) else ""
        )
    if not partial or "isDefault" in data:
        result["is_default"] = 1 if data.get("isDefault") else 0
    return {key: value for key, value in result.items() if key in _FIELDS}


def _clear_other_defaults(conn: Any, kind: str, keep_id: int | None = None) -> None:
    """同一类别只保留一个默认模板，其余默认标记清掉。"""
    if keep_id is None:
        conn.execute(
            "UPDATE sys_doc_template SET is_default = 0 WHERE kind = ? AND is_deleted = 0",
            (kind,),
        )
    else:
        conn.execute(
            "UPDATE sys_doc_template SET is_default = 0 "
            "WHERE kind = ? AND id <> ? AND is_deleted = 0",
            (kind, keep_id),
        )


@bp.get("")
def list_templates() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = DocTemplateRepository(conn)
    filters: dict[str, Any] = {}
    kind = request.args.get("kind")
    if kind:
        filters["kind"] = kind
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [decorate(row) for row in payload["list"]]
    return ok(payload)


@bp.get("/options")
def template_options() -> Response:
    """能力页下拉：只返回同类模板，不分页。"""
    conn = db()
    kind = check_choice(str(request.args.get("kind") or ""), TEMPLATE_KINDS, "kind")
    if not kind:
        raise AppError(ErrorCode.PARAM_ERROR, "请指定模板类别")
    page = DocTemplateRepository(conn).list({"kind": kind}, None, page_num=1, page_size=100)
    return ok({"list": [decorate(row) for row in page.list]})


@bp.get("/<pk>")
def get_template(pk: str) -> Response:
    conn = db()
    row = DocTemplateRepository(conn).get(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模板不存在")
    return ok(decorate(row))


@bp.post("/uploads/cleanup")
def cleanup_uploads() -> Response:
    """清理取消保存后遗留的原件：只删没有被任何模板引用的落盘文件。"""
    names = body().get("storedNames")
    if not isinstance(names, list):
        raise AppError(ErrorCode.PARAM_ERROR, "storedNames 必须是数组")
    conn = db()
    referenced = DocTemplateRepository(conn).stored_names()
    removed = doc_template_service.cleanup_unreferenced_files(
        _templates_root(), [str(item) for item in names], referenced
    )
    return ok({"removed": removed})


@bp.post("/upload")
def upload_template() -> Response:
    """上传模板文件：解析正文 / 列名 / 版式，同时把原文件留一份在本机。

    留原文件是为了模板可以再下载回去；是否保存这条模板仍由用户在表单里确认。
    """
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise AppError(ErrorCode.PARAM_ERROR, "请选择要上传的模板文件")
    data = uploaded.read()
    parsed = doc_template_service.parse_template_file(data, uploaded.filename)
    stored = doc_template_service.save_template_file(_templates_root(), parsed.file_name, data)
    return ok({
        "fileName": parsed.file_name,
        "fileType": parsed.file_type,
        "fileSize": parsed.file_size,
        "storedName": stored,
        "content": parsed.content,
        "columns": parsed.columns,
        "styleHint": parsed.style_hint,
    })


@bp.get("/<pk>/file")
def download_template_file(pk: str) -> Response:
    """下载模板最近一次上传的原始文件。"""
    conn = db()
    row = DocTemplateRepository(conn).get(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模板不存在")
    stored = str(row.get("stored_name") or "")
    if not stored:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "该模板没有上传过文件")
    path = doc_template_service.template_file_path(_templates_root(), stored)
    if path is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模板文件已不在本机，请重新上传")
    display = str(row.get("file_name") or path.name)
    return send_file(
        path,
        as_attachment=True,
        download_name=display,
        mimetype=doc_template_service.mimetype_of(display),
    )


@bp.post("")
def create_template() -> Response:
    conn = db()
    repo = DocTemplateRepository(conn)
    payload = _payload(body())
    if payload.get("is_default"):
        _clear_other_defaults(conn, str(payload.get("kind") or "case"))
    row_id = repo.insert(payload)
    commit(conn)
    audit(
        conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_DOC_TEMPLATE,
        row_id, str(payload.get("name") or ""), {"kind": payload.get("kind")},
    )
    return ok(decorate(repo.get(row_id) or {}))


@bp.put("/<pk>")
def update_template(pk: str) -> Response:
    conn = db()
    repo = DocTemplateRepository(conn)
    target = path_int(pk)
    existing = repo.get(target)
    if not existing:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模板不存在")
    payload = _payload(body(), partial=True)
    if payload.get("is_default"):
        kind = str(payload.get("kind") or existing.get("kind") or "case")
        _clear_other_defaults(conn, kind, keep_id=target)
    repo.update(target, payload)
    commit(conn)
    if "stored_name" in payload:
        old = str(existing.get("stored_name") or "")
        if old and old != str(payload.get("stored_name") or ""):
            doc_template_service.delete_template_file(_templates_root(), old)
    audit(
        conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_DOC_TEMPLATE,
        target, str(payload.get("name") or existing.get("name") or ""),
        {"fields": sorted(payload.keys())},
    )
    return ok(decorate(repo.get(target) or {}))


@bp.delete("/<pk>")
def delete_template(pk: str) -> Response:
    conn = db()
    repo = DocTemplateRepository(conn)
    target = path_int(pk)
    existing = repo.get(target)
    if not existing:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模板不存在")
    repo.soft_delete(target)
    commit(conn)
    # 模板删了就没入口再下载，落盘文件跟着清掉，不在本机堆积没人用的原件。
    doc_template_service.delete_template_file(
        _templates_root(), str(existing.get("stored_name") or "")
    )
    audit(
        conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_DOC_TEMPLATE,
        target, str(existing.get("name") or ""),
    )
    return ok({"id": target})
