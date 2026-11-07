"""AI 执行记录 API：证据、缺项、风险点与人工复核结论。

    GET  /api/v1/ai-records              列表（可按能力、复核状态、风险等级过滤）
    GET  /api/v1/ai-records/<pk>         详情
    POST /api/v1/ai-records/<pk>/review  写人工复核结论（采纳 / 驳回 + 备注）
"""

from __future__ import annotations

from flask import Blueprint, Response, request

from ..common.validators import optional_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.ai_record_repo import AIRecordRepository
from ..services import ai_record_service, audit_service
from .helpers import (
    audit, body, commit, db, keyword_of, page_args, page_payload, path_int, scope_arg,
)

bp = Blueprint("ai_records", __name__)


@bp.get("")
def list_records() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = AIRecordRepository(conn)
    filters: dict[str, object] = {}
    for key, column in (
        ("capability", "capability"),
        ("riskLevel", "risk_level"),
        ("sessionId", "session_id"),
        ("taskId", "task_id"),
    ):
        value = request.args.get(key)
        if value in (None, ""):
            continue
        filters[column] = optional_int(value, key) if column in ("session_id", "task_id") else value
    # 「待复核」与「无需复核」在库里都是 pending，靠 review_required 区分；
    # 直接按 review_status 过滤会把两类混在一起。
    review_status = str(request.args.get("reviewStatus") or "").strip()
    if review_status in ("pending", "not_required"):
        filters["review_status"] = ai_record_service.REVIEW_PENDING
        filters["review_required"] = review_status == "pending"
    elif review_status:
        filters["review_status"] = review_status
    page = repo.page_scoped(filters, keyword_of() or None, scope_arg(), page_num, page_size)
    payload = page_payload(page)
    payload["list"] = [repo.decorate(row) for row in payload["list"]]
    project_id = scope_arg()
    payload["stats"] = {
        "pending": repo.count_by_review(
            ai_record_service.REVIEW_PENDING, project_id, review_required=True
        ),
        "notRequired": repo.count_by_review(
            ai_record_service.REVIEW_PENDING, project_id, review_required=False
        ),
        "approved": repo.count_by_review(ai_record_service.REVIEW_APPROVED, project_id),
        "rejected": repo.count_by_review(ai_record_service.REVIEW_REJECTED, project_id),
    }
    return ok(payload)


@bp.get("/<pk>")
def get_record(pk: str) -> Response:
    conn = db()
    repo = AIRecordRepository(conn)
    detail = repo.get_detail(path_int(pk))
    if not detail:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不存在")
    return ok(repo.decorate(detail, full=True))


@bp.post("/<pk>/review")
def review_record(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    data = body()
    status = require_str(data.get("status"), "status", 20)
    note = str(data.get("note") or "").strip()[:1000]
    result = ai_record_service.apply_review(conn, target, status, note)
    commit(conn)
    audit(
        conn,
        audit_service.AUDIT_ACTION_REVIEW,
        audit_service.RESOURCE_AI_RECORD,
        target,
        str(data.get("capabilityLabel") or ""),
        {"status": status, "note": note},
    )
    return ok(result)
