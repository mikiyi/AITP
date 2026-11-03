"""公共路由：健康检查。"""

from __future__ import annotations

from flask import Blueprint, Response, current_app

from ...envelope import ok
from ...common.time_utils import utcnow_iso
from ... import __version__

bp = Blueprint("common", __name__)


@bp.get("/health")
def health() -> Response:
    return ok(
        {
            "status": "ok",
            "appVersion": __version__,
            "aiConfigured": False,
            "serverTime": utcnow_iso(),
        }
    )
