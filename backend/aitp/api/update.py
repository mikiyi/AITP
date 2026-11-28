"""检查更新 API：git fetch 探测 origin/master，快进合并后后台重建前端。"""

from __future__ import annotations

from flask import Blueprint, Response

from ..envelope import ok
from ..services import update_service

bp = Blueprint("update", __name__)


@bp.get("/update-check")
def update_check() -> Response:
    return ok(update_service.check())


@bp.get("/update-status")
def update_status() -> Response:
    return ok(update_service.status())


@bp.post("/update-apply")
def update_apply() -> Response:
    return ok(update_service.apply())
