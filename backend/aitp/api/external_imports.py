"""外部平台只读导入 API（三期 GAP-40）。

只读单向：拉取需求/用例/缺陷进 AITP，绝不回写源平台。
凭据只在请求体里 transient 传入，落系统密钥库；库内仅存引用与掩码。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.validators import require_int
from ..envelope import ok
from ..services import audit_service, external_import_service
from ..services.external_import_service import default_mapping
from .helpers import audit, body, commit, db, require_project

bp = Blueprint("external_imports", __name__)


def _opt_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    return require_int(value, field)


def _platform_arg(data: dict[str, Any]) -> str:
    return str(data.get("platform") or "zentao")


@bp.get("/platforms")
def list_platforms() -> Response:
    platforms = []
    for key, label in external_import_service.PLATFORM_LABELS.items():
        supported = external_import_service.PLATFORM_KINDS[key]
        platforms.append({
            "key": key,
            "label": label,
            "kinds": list(supported),
            "kindsLabel": [
                {"key": kind, "label": external_import_service.KIND_LABELS[kind]}
                for kind in supported
            ],
            "defaultMapping": default_mapping(key),
        })
    return ok({
        "platforms": platforms,
        "kinds": [
            {"key": key, "label": label}
            for key, label in external_import_service.KIND_LABELS.items()
        ],
        "defaultMapping": default_mapping("zentao"),
    })


@bp.get("/config")
def get_config() -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    platform = str(request.args.get("platform") or "zentao")
    return ok(external_import_service.get_config(conn, project_id, platform))


@bp.post("/config")
def save_config() -> Response:
    conn = db()
    data = body()
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    platform = _platform_arg(data)
    result = external_import_service.save_config(
        conn,
        project_id=project_id,
        platform=platform,
        base_url=str(data.get("baseUrl") or ""),
        username=str(data.get("username") or ""),
        password=str(data.get("password") or ""),
        server_username=str(data.get("serverUsername") or ""),
        server_password=str(data.get("serverPassword") or ""),
        product_id=str(data.get("productId") or ""),
        extra=data.get("extra"),
    )
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_EXTERNAL_IMPORT,
          project_id, platform, {"kind": "config_save"})
    commit(conn)
    return ok(result)


@bp.delete("/config")
def delete_config() -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    platform = str(request.args.get("platform") or "zentao")
    external_import_service.delete_config(conn, project_id, platform)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_EXTERNAL_IMPORT,
          project_id, platform, {"kind": "config_delete"})
    commit(conn)
    return ok({"deleted": True})


@bp.get("/mappings")
def list_mappings() -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    platform = str(request.args.get("platform") or "zentao")
    return ok(external_import_service.list_mappings(conn, project_id, platform))


@bp.post("/mappings")
def create_mapping() -> Response:
    conn = db()
    data = body()
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    result = external_import_service.save_mapping(
        conn,
        project_id=project_id,
        platform=_platform_arg(data),
        name=str(data.get("name") or ""),
        mapping=data.get("mapping"),
        is_default=bool(data.get("isDefault")),
    )
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_EXTERNAL_IMPORT,
          project_id, str(result.get("name") or ""), {"kind": "mapping_create"})
    commit(conn)
    return ok(result)


@bp.put("/mappings/<int:mapping_id>")
def update_mapping(mapping_id: int) -> Response:
    conn = db()
    data = body()
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    result = external_import_service.save_mapping(
        conn,
        project_id=project_id,
        platform=_platform_arg(data),
        name=str(data.get("name") or ""),
        mapping=data.get("mapping"),
        is_default=bool(data.get("isDefault")),
        mapping_id=mapping_id,
    )
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_EXTERNAL_IMPORT,
          project_id, str(result.get("name") or ""), {"kind": "mapping_update"})
    commit(conn)
    return ok(result)


@bp.delete("/mappings/<int:mapping_id>")
def delete_mapping(mapping_id: int) -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    external_import_service.delete_mapping(conn, mapping_id, project_id=project_id)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_EXTERNAL_IMPORT,
          project_id, "", {"kind": "mapping_delete", "mappingId": mapping_id})
    commit(conn)
    return ok({"deleted": True})


@bp.post("/preview")
def preview_import() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    result = external_import_service.preview(
        conn,
        project_id=project_id,
        platform=_platform_arg(data),
        mapping_id=_opt_int(data.get("mappingId"), "mappingId"),
        max_results=data.get("maxResults"),
        kinds=data.get("kinds"),
    )
    return ok(result)


@bp.post("/import")
def run_import() -> Response:
    conn = db()
    data = body()
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    raw_kinds = data.get("kinds")
    kinds = (
        [str(item) for item in raw_kinds]
        if isinstance(raw_kinds, list)
        else str(raw_kinds or "").split(",")
    )
    result = external_import_service.import_items(
        conn,
        project_id=project_id,
        platform=_platform_arg(data),
        kinds=kinds,
        mapping_id=_opt_int(data.get("mappingId"), "mappingId"),
        max_results=data.get("maxResults"),
    )
    audit(conn, audit_service.AUDIT_ACTION_IMPORT, audit_service.RESOURCE_EXTERNAL_IMPORT,
          project_id, "", {
              "kind": "external_import",
              "batchId": result["batchId"],
              "imported": result["imported"],
              "skipped": result["skipped"],
          })
    commit(conn)
    return ok(result)


@bp.get("/batches")
def list_batches() -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    return ok(external_import_service.list_batches(conn, project_id))


@bp.get("/batches/<int:batch_id>")
def get_batch(batch_id: int) -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    return ok(external_import_service.batch_detail(conn, batch_id, project_id=project_id))