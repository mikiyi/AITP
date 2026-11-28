"""检查更新 API：探测 Gitee master 版本、后台断点续传下载、暂存为「下次启动生效」。"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, request

from ..envelope import fail, ok
from ..errors import ErrorCode
from ..services import update_service

bp = Blueprint("update", __name__)


def _data_dir() -> Path:
    return Path(str(current_app.config["AITP_DB_PATH"])).parent


@bp.get("/update-check")
def update_check() -> Response:
    return ok(update_service.check_update())


@bp.get("/update-status")
def update_status() -> Response:
    payload = update_service.status()
    payload["pending"] = update_service.pending_info(_data_dir())
    return ok(payload)


@bp.post("/update-download")
def update_download() -> Response:
    body = request.get_json(silent=True) or {}
    version = str(body.get("version") or "").strip()
    if not version:
        return fail(ErrorCode.PARAM_ERROR, "缺少目标版本号")
    return ok(update_service.start_download(_data_dir(), version))
