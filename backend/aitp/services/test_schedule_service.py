"""P2 定时调度：按间隔把 场景/脚本 投递进 job_queue（复用并发上限/隔离/取消）。

CI webhook 触发原有（GAP-24）；本模块只补「定时」触发源。调度线程模式与
backup_schedule 一致：每个库一个守护线程，重复 start 不叠加。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..common.time_utils import utcnow_iso
from ..db.connection import get_standalone_conn
from ..repositories.schedule_repo import TestScheduleRepository
from . import job_queue

KIND_SCENARIO = "scenario.run"
POLL_SECONDS = 5.0

_schedulers: dict[str, tuple[threading.Thread, threading.Event]] = {}
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def run_scenario_job(conn: sqlite3.Connection, payload: dict[str, Any],
                     task: dict[str, Any], db_path: Path) -> dict[str, Any]:
    from ..repositories.api_scenario_repo import ApiScenarioRepository
    from . import api_scenario_service

    scenario = ApiScenarioRepository(conn).get(int(payload.get("scenarioId") or 0))
    if scenario is None:
        raise ValueError("调度目标场景不存在")
    result = api_scenario_service.run_with_data(conn, dict(scenario), scenario.get("env_id"))
    return {"passed": result["passed"], "runNo": result["runNo"]}


job_queue.register(KIND_SCENARIO, run_scenario_job)


def _tick(db_path: Path) -> int:
    conn = get_standalone_conn(db_path)
    fired = 0
    try:
        repo = TestScheduleRepository(conn)
        now = _now()
        for row in repo.list_all():
            if not row.get("enabled") or row.get("is_deleted"):
                continue
            due = _parse(row.get("next_run_at"))
            if due is not None and due > now:
                continue
            target_type = str(row.get("target_type") or "scenario")
            if target_type == "scenario":
                job_queue.submit(conn, kind=KIND_SCENARIO,
                                 title=f"定时场景 · {row.get('name')}",
                                 payload={"scenarioId": int(row["target_id"])}, priority=60)
            else:
                job_queue.submit(conn, kind="automation.run",
                                 title=f"定时脚本 · {row.get('name')}",
                                 payload={"scriptId": int(row["target_id"])}, priority=60)
            interval = max(1, int(row.get("interval_minutes") or 60))
            repo.update(int(row["id"]), {
                "last_run_at": utcnow_iso(),
                "next_run_at": (now + timedelta(minutes=interval)).isoformat(),
            })
            fired += 1
        conn.commit()
    finally:
        conn.close()
    return fired


def _loop(db_path: Path, stop: threading.Event) -> None:
    while not stop.wait(POLL_SECONDS):
        try:
            _tick(db_path)
        except Exception:  # noqa: BLE001 - 调度单轮失败不退出线程
            continue


def start_scheduler(db_path: Path) -> None:
    key = str(db_path)
    with _lock:
        existing = _schedulers.get(key)
        if existing is not None and existing[0].is_alive():
            return
        stop = threading.Event()
        thread = threading.Thread(target=_loop, args=(db_path, stop),
                                  name="aitp-test-scheduler", daemon=True)
        _schedulers[key] = (thread, stop)
        thread.start()


def stop_scheduler(db_path: Path | None = None) -> None:
    with _lock:
        keys = [str(db_path)] if db_path is not None else list(_schedulers)
        for key in keys:
            item = _schedulers.pop(key, None)
            if item is not None:
                item[1].set()