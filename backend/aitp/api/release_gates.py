"""发布准入 API：按轮次评估质量门禁并留档。"""

from __future__ import annotations

from flask import Blueprint, Response

from ..common.validators import optional_int, optional_str, require_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.release_gate_repo import ReleaseGateRepository, gate_summary
from ..services import audit_service, notification_service, release_gate_service
from .helpers import attach_version_codes, audit, body, commit, db, keyword_of, page_args, page_payload, path_int, query_filters

bp = Blueprint("release_gates", __name__)

FILTERS = {
    "projectId": "project_id",
    "versionId": "version_id",
    "runId": "run_id",
    "status": "status",
}


@bp.get("")
def list_gates() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = ReleaseGateRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = attach_version_codes(
        conn, [gate_summary(repo.get_detail(int(row["id"])) or row) for row in payload["list"]]
    )
    return ok(payload)


@bp.get("/<pk>")
def get_gate(pk: str) -> Response:
    conn = db()
    repo = ReleaseGateRepository(conn)
    row = repo.get_detail(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "发布准入记录不存在")
    payload = gate_summary(row)
    return ok(attach_version_codes(conn, [payload])[0])


@bp.post("/preview")
def preview_gate() -> Response:
    """只算不留档：调整阈值或查看实时结论时用。"""
    conn = db()
    data = body()
    result = release_gate_service.evaluate(
        conn,
        project_id=require_int(data.get("projectId"), "projectId"),
        run_id=require_int(data.get("runId"), "runId"),
        version_id=optional_int(data.get("versionId"), "versionId"),
        thresholds=data.get("thresholds") if isinstance(data.get("thresholds"), dict) else None,
    )
    return ok(result)


@bp.post("")
def create_gate() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    run_id = require_int(data.get("runId"), "runId")
    thresholds = data.get("thresholds") if isinstance(data.get("thresholds"), dict) else None
    payload = release_gate_service.evaluate_and_save(
        conn,
        project_id=project_id,
        run_id=run_id,
        name=optional_str(data.get("name"), "name", 200) or "发布准入检查",
        version_id=optional_int(data.get("versionId"), "versionId"),
        thresholds=thresholds,
        created_by=optional_str(data.get("createdBy"), "createdBy", 64) or "",
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_RELEASE_GATE,
          int(payload["id"]), str(payload.get("name") or ""),
          {"runId": run_id, "status": payload.get("status")})
    notification_service.notify_gate_blocked(conn, payload)
    commit(conn)
    return ok(payload)


@bp.delete("/<pk>")
def delete_gate(pk: str) -> Response:
    conn = db()
    repo = ReleaseGateRepository(conn)
    target = path_int(pk)
    row = repo.get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "发布准入记录不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_RELEASE_GATE, target,
          str(row.get("name") or ""))
    return ok({"id": target, "deleted": True})
