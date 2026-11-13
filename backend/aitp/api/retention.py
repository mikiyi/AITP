"""数据留存与归档 API（三期 GAP-44）。

配置各类型保留天数与到期动作 → 预览到期量 → 手动/自动清理 →
归档区检索与回看原文。清理默认走任务队列（async=true），也可同步小跑。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Blueprint, Response, request

from ..common.validators import optional_int, optional_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.archive_repo import ArchiveBundleRepository
from ..services import audit_service, job_queue, retention_service
from .helpers import audit, body, commit, db, path_int, page_args

bp = Blueprint("retention", __name__)


@bp.get("/settings")
def get_settings_api() -> Response:
    conn = db()
    return ok(retention_service.get_settings(conn))


@bp.put("/settings")
def update_settings_api() -> Response:
    conn = db()
    payload = retention_service.update_settings(conn, body())
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, "retention_settings", 0,
          "数据留存配置", {"items": [item["dataType"] for item in payload["items"]]})
    return ok(payload)


@bp.get("/preview")
def preview_api() -> Response:
    conn = db()
    return ok(retention_service.preview(conn))


@bp.post("/cleanup")
def cleanup_api() -> Response:
    conn = db()
    data = body()
    dry_run = bool(data.get("dryRun"))
    data_types = data.get("dataTypes")
    if data_types is not None and not isinstance(data_types, list):
        raise AppError(ErrorCode.PARAM_ERROR, "dataTypes 必须是数组")
    if dry_run:
        return ok(retention_service.run_cleanup(conn, _db_path(conn), data_types=data_types,
                                                dry_run=True, created_by="local"))
    task = job_queue.submit(
        conn,
        kind="retention.cleanup",
        title="数据留存到期清理",
        payload={"dataTypes": data_types, "createdBy": "manual"},
        priority=90,
    )
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, "retention_cleanup", int(task["id"]),
          "数据留存到期清理（任务队列）", {"dataTypes": data_types or retention_service.DATA_TYPES})
    commit(conn)
    return ok({"queued": True, "task": task})


@bp.get("/archives")
def list_archives_api() -> Response:
    conn = db()
    page_num, page_size = page_args()
    data_type = request.args.get("dataType")
    if data_type and data_type not in retention_service.DATA_TYPES:
        raise AppError(ErrorCode.PARAM_ERROR, "未知数据类型")
    payload = retention_service.list_bundles(
        conn,
        page_num=page_num,
        page_size=page_size,
        keyword=request.args.get("keyword") or None,
        data_type=data_type,
    )
    payload["dataTypes"] = [
        {"value": item, "label": retention_service.TYPE_LABELS[item]}
        for item in retention_service.DATA_TYPES
    ]
    return ok(payload)


@bp.get("/archives/<pk>")
def get_archive_api(pk: str) -> Response:
    conn = db()
    return ok(retention_service.get_bundle(conn, path_int(pk)))


@bp.get("/archives/<pk>/items/<ref>")
def read_archive_item_api(pk: str, ref: str) -> Response:
    conn = db()
    original = retention_service.read_item(conn, path_int(pk), path_int(ref, "ref"))
    return ok({"original": original})


@bp.delete("/archives/<pk>")
def delete_archive_api(pk: str) -> Response:
    """删除归档包裹与索引（不删本机 zip，避免误删；仅移出检索区）。"""
    conn = db()
    target = path_int(pk)
    row = ArchiveBundleRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "归档包裹不存在")
    ArchiveBundleRepository(conn).soft_delete(target)
    conn.execute(
        "UPDATE biz_archive_item SET is_deleted = 1 WHERE bundle_id = ? AND is_deleted = 0",
        (target,),
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, "archive_bundle", target,
          str(row.get("bundle_no") or ""), {"dataType": row.get("data_type")})
    return ok({"id": target, "deleted": True})


def _db_path(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        raise AppError(ErrorCode.PARAM_ERROR, "无法确定数据库路径")
    return Path(str(row[2]))
