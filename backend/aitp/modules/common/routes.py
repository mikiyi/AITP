"""公共路由：健康检查。"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app

from ...envelope import ok
from ...common.time_utils import utcnow_iso
from ...db.connection import database_status
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
            "database": database_status(Path(current_app.config["AITP_DB_PATH"])),
        }
    )
