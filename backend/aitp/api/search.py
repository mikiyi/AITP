"""全局搜索 API：一次检索需求、用例、缺陷、知识。

    GET /api/v1/search?q=登录&types=requirement,case
"""

from __future__ import annotations

from flask import Blueprint, Response, request

from ..common.validators import require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import search_service
from .helpers import db

bp = Blueprint("search", __name__)

_MAX_LIMIT = 50


@bp.get("")
def global_search() -> Response:
    conn = db()
    raw = request.args.get("q") or request.args.get("keyword")
    keyword = require_str(raw, "q", 200)
    types = _types_of(request.args.get("types"))
    project_raw = request.args.get("projectId")
    project_id = require_int(project_raw, "projectId") if project_raw else None
    limit_raw = request.args.get("limit")
    limit = require_int(limit_raw, "limit") if limit_raw else 20
    limit = max(1, min(limit, _MAX_LIMIT))
    return ok(search_service.search_all(
        conn, keyword, types=types, project_id=project_id, limit=limit,
    ))


def _types_of(raw: str | None) -> tuple[str, ...]:
    """解析 types 参数；未知类型直接报错，避免静默返回空结果。"""
    if not raw:
        return search_service.SEARCH_TYPES
    picked: list[str] = []
    for item in raw.split(","):
        name = item.strip()
        if not name:
            continue
        if name not in search_service.SEARCH_TYPES:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"types 只支持 {'/'.join(search_service.SEARCH_TYPES)}",
            )
        if name not in picked:
            picked.append(name)
    return tuple(picked) or search_service.SEARCH_TYPES
