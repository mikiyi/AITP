"""导航未读角标 API。

    GET  /api/v1/badges                      各分区未读数
    POST /api/v1/badges/<section>/increase   +N（前端补记或联调用）
    POST /api/v1/badges/<section>/clear      进入页面查看后清零
"""

from __future__ import annotations

from flask import Blueprint, Response

from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import nav_badge_service
from .helpers import body, commit, db

bp = Blueprint("badges", __name__)


def _section(value: str) -> str:
    value = str(value or "").strip()
    if value not in nav_badge_service.SECTIONS:
        raise AppError(ErrorCode.PARAM_ERROR, "未知的角标分区")
    return value


@bp.get("")
def list_badges() -> Response:
    conn = db()
    return ok(nav_badge_service.as_dict(conn))


@bp.post("/<section>/increase")
def increase_badge(section: str) -> Response:
    conn = db()
    data = body()
    try:
        count = int(data.get("count") or 1)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, "count 必须是整数") from None
    unread = nav_badge_service.increase(conn, _section(section), count)
    commit(conn)
    return ok({"section": section, "unread": unread})


@bp.post("/<section>/clear")
def clear_badge(section: str) -> Response:
    conn = db()
    before = nav_badge_service.clear(conn, _section(section))
    commit(conn)
    return ok({"section": section, "cleared": before})
