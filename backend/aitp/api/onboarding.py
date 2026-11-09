"""首启引导状态 API（GAP-18，二期第 0 批）。

状态落在 sys_config 两个系统键（迁移 045 种入）：
  onboarding.completed    引导是否已结束（完成或跳过都算），false 时前端首页弹引导
  onboarding.reopened_at  最近一次从帮助页重开引导的时间，仅作展示

POST 动作三种：complete（完成）/ skip（跳过）/ reopen（重开）。
"""

from __future__ import annotations

from flask import Blueprint, Response

from ..common.time_utils import utcnow_iso
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.sys_config_repo import SysConfigRepository
from ..services import audit_service, demo_seed_service
from .helpers import audit, body, commit, db

bp = Blueprint("onboarding", __name__)

_KEY_COMPLETED = "onboarding.completed"
_KEY_REOPENED = "onboarding.reopened_at"

_ACTIONS = ("complete", "skip", "reopen")


def _state(repo: SysConfigRepository) -> dict[str, object]:
    completed = str(repo.get_value(_KEY_COMPLETED, "false")).lower() == "true"
    return {
        "completed": completed,
        "reopenedAt": str(repo.get_value(_KEY_REOPENED, "") or ""),
        # 前端只看这个字段决定要不要弹引导，completed 留作展示
        "showGuide": not completed,
    }


def _set(repo: SysConfigRepository, key: str, value: str, value_type: str) -> None:
    row = repo.get(key)
    description = str(row.get("description") or "") if row else ""
    repo.set_value(key, value, value_type, description, "global", 1)


@bp.get("")
def get_onboarding() -> Response:
    repo = SysConfigRepository(db())
    return ok(_state(repo))


@bp.post("")
def update_onboarding() -> Response:
    payload = body()
    action = str(payload.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"action 只能是 {' / '.join(_ACTIONS)}",
        )
    conn = db()
    repo = SysConfigRepository(conn)
    if action == "reopen":
        _set(repo, _KEY_COMPLETED, "false", "bool")
        _set(repo, _KEY_REOPENED, utcnow_iso(), "string")
    else:
        _set(repo, _KEY_COMPLETED, "true", "bool")
    commit(conn)
    audit(
        conn,
        audit_service.AUDIT_ACTION_CONFIG,
        "onboarding",
        None,
        "首启引导",
        {"action": action},
    )
    return ok(_state(repo))


@bp.post("/demo")
def inject_demo() -> Response:
    """追加式注入示例项目：只重建 DEMO-EC 自身，绝不清空其他项目。"""
    conn = db()
    counts = demo_seed_service.seed_append(conn)
    commit(conn)
    audit(
        conn,
        audit_service.AUDIT_ACTION_IMPORT,
        "onboarding",
        None,
        "示例项目",
        {"code": demo_seed_service.DEMO_CODE, "counts": counts},
    )
    return ok({"code": demo_seed_service.DEMO_CODE, "counts": counts})
