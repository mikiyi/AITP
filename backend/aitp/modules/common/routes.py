"""公共路由：健康检查。

健康项只报「系统里真的有数据源」的部分：
  - aiConfigured：复用 ai/resolver 的候选解析，判定口径与「AI 设置」页的降级列表一致
    （存在启用中的档案，且该档案能取到可用密钥或是本地档案）；
  - database：db.connection.database_status() 的连通性快照，已含 latencyMs；
  - dbWrite：db/write_guard.write_stats() 的写锁计数（提交次数、排队等待、busy 重试、超时）。
    GAP-26 的「锁库归零」必须有能被外部读到的口径，否则门禁只能靠翻日志猜；
  - aiProfileCount / aiCandidateCount：档案数量与实际可用候选数；
  - knowledgePendingDrafts：知识库待审池积压；
  - taskQueue：后台任务队列的排队/执行中条数（AITP 自带 job_queue，不是外部队列）。
新增字段一律只做加法，不改名、不删旧字段，保证老前端可读。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app

from ... import __version__
from ...ai import resolver
from ...common.time_utils import utcnow_iso
from ...db import write_guard
from ...db.connection import database_status, get_db
from ...envelope import ok
from ...repositories.ai_profile_repo import AIProfileRepository
from ...repositories.knowledge_draft_repo import KnowledgeDraftRepository
from ...services import job_queue

bp = Blueprint("common", __name__)


def _secret_key_path() -> Path | None:
    raw = current_app.config.get("AITP_SECRET_KEY_PATH")
    return Path(str(raw)) if raw else None


def _ai_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """AI 真实可用性：档案数量 + resolver 判定为可用的候选。

    判定逻辑不在这里重写——resolver.resolve_candidates 已经按
    「需要密钥的供应商必须取到密钥 / 本地档案免密钥」筛过一轮，
    它返回的候选就是「AI 设置」页展示的那份降级顺序表。
    """
    active_profiles = AIProfileRepository(conn).list_active()
    candidates = [
        candidate
        for candidate in resolver.resolve_candidates(
            key_path=_secret_key_path(), require_key=False
        )
        # profile_id 为空的是环境变量折算出来的虚拟档案，不代表用户配了档案
        if candidate.profile_id is not None
    ]
    return {
        "aiConfigured": bool(candidates),
        "aiProfileCount": len(active_profiles),
        "aiCandidateCount": len(candidates),
    }


def _safe(callable_: Any, default: Any) -> Any:
    """健康检查不许因为某一项取数失败就整体 500。"""
    try:
        return callable_()
    except Exception:  # noqa: BLE001 - 观测项失败时退回默认值
        return default


@bp.get("/health")
def health() -> Response:
    db_path = Path(current_app.config["AITP_DB_PATH"])
    payload: dict[str, Any] = {
        "status": "ok",
        "appVersion": __version__,
        "aiConfigured": False,
        "serverTime": utcnow_iso(),
        "database": database_status(db_path),
        # 写锁计数是进程内的：跨进程看不到别人的排队，所以只报本进程这一份
        "dbWrite": _safe(write_guard.write_stats, {}),
    }

    # 取数失败只影响这一项，其余健康项照常返回
    conn = _safe(get_db, None)
    if conn is not None:
        payload.update(_safe(lambda: _ai_snapshot(conn), {}) or {})
        payload["knowledgePendingDrafts"] = _safe(
            lambda: KnowledgeDraftRepository(conn).pending_count(), 0
        )
        stats = _safe(lambda: job_queue.stats(conn), {}) or {}
        payload["taskQueue"] = {
            "queued": int(stats.get(job_queue.TASK_QUEUED) or 0),
            "running": int(stats.get(job_queue.TASK_RUNNING) or 0),
        }
    else:
        payload["aiConfigured"] = False
        payload["aiProfileCount"] = 0
        payload["aiCandidateCount"] = 0
        payload["knowledgePendingDrafts"] = 0
        payload["taskQueue"] = {"queued": 0, "running": 0}

    return ok(payload)
