"""AI 修复建议 API（四期专项 A）。

列表 / 详情 / 从执行记录一键生成 / 采纳 / 忽略。所有读写按 projectId 收敛。
"""

from __future__ import annotations

from flask import Blueprint, Response, request

from ..common.validators import check_choice, optional_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import fix_suggestion_service
from .helpers import body, commit, db, page_args, path_int, require_project

bp = Blueprint("fix_suggestions", __name__)


def _project_id(required: bool = True) -> int | None:
    raw = request.args.get("projectId")
    if raw in (None, ""):
        if required:
            raise AppError(ErrorCode.PARAM_ERROR, "缺少 projectId，无法校验修复建议归属")
        return None
    return optional_int(raw, "projectId")


@bp.get("")
def list_suggestions_api() -> Response:
    conn = db()
    project_id = _project_id()
    assert project_id is not None
    require_project(conn, project_id)
    page_num, page_size = page_args()
    status = request.args.get("status") or None
    if status:
        status = check_choice(status, fix_suggestion_service.STATUSES, "status")
    payload = fix_suggestion_service.list_suggestions(
        conn, project_id=project_id, status=status,
        page_num=page_num, page_size=page_size,
    )
    payload["statuses"] = [
        {"value": item, "label": fix_suggestion_service.STATUS_LABELS[item]}
        for item in fix_suggestion_service.STATUSES
    ]
    payload["targets"] = [
        {"value": item, "label": fix_suggestion_service.TARGET_LABELS[item]}
        for item in fix_suggestion_service.TARGETS
    ]
    return ok(payload)


@bp.get("/<pk>")
def get_suggestion_api(pk: str) -> Response:
    conn = db()
    project_id = _project_id()
    assert project_id is not None
    return ok(fix_suggestion_service.get_suggestion(conn, path_int(pk), project_id))


@bp.post("")
def generate_suggestion_api() -> Response:
    """从一次执行记录生成建议；无模型时降级返回机械 diff（generated=false）。"""
    conn = db()
    data = body()
    execution_id = optional_int(data.get("executionId"), "executionId")
    if not execution_id:
        raise AppError(ErrorCode.PARAM_ERROR, "缺少 executionId")
    project_id = optional_int(data.get("projectId"), "projectId")
    profile_id = optional_int(data.get("profileId"), "profileId")
    result = fix_suggestion_service.generate(
        conn, execution_id,
        project_id=project_id, profile_id=profile_id,
    )
    commit(conn)
    return ok(result)


@bp.post("/<pk>/adopt")
def adopt_suggestion_api(pk: str) -> Response:
    conn = db()
    data = body()
    project_id = optional_int(data.get("projectId"), "projectId")
    if project_id is None:
        project_id = _project_id(required=False)
    result = fix_suggestion_service.adopt(conn, path_int(pk), project_id=project_id)
    commit(conn)
    return ok(result)


@bp.post("/<pk>/dismiss")
def dismiss_suggestion_api(pk: str) -> Response:
    conn = db()
    data = body()
    project_id = optional_int(data.get("projectId"), "projectId")
    if project_id is None:
        project_id = _project_id(required=False)
    result = fix_suggestion_service.dismiss(conn, path_int(pk), project_id=project_id)
    commit(conn)
    return ok(result)
