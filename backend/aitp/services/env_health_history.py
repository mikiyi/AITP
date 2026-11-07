"""测试环境健康历史：服务端定时采样，进首页直接出曲线。

曲线只缓存在内存里（最近 1 小时，15 秒一个点），不落库、不发往外部服务。
服务启动后即开始采样，离开首页也不中断；前端进页面直接读历史，
不必每次重新探测、重新等曲线画出来。
"""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Any

from ..common.time_utils import parse_iso, utcnow_iso
from ..db.connection import get_standalone_conn
from ..repositories.test_env_repo import TestEnvRepository
from . import env_probe_service

#: 采样间隔与保留窗口，与首页曲线的口径保持一致
SAMPLE_INTERVAL = 15.0
WINDOW_SECONDS = 60 * 60
#: 单条曲线最多保留的点数，约等于窗口 / 间隔
HISTORY_LIMIT = 260
SIDES = ("app", "db")

_series: dict[str, list[tuple[int, float]]] = {}
_latest: dict[int, dict[str, Any]] = {}
_series_lock = threading.Lock()
_samplers_lock = threading.Lock()
_samplers: dict[str, tuple[threading.Thread, threading.Event]] = {}


def _stamp_ms(metrics: dict[str, Any], fallback: int) -> int:
    parsed = parse_iso(str(metrics.get("sampledAt") or ""))
    return int(parsed.timestamp() * 1000) if parsed is not None else fallback


def _push_locked(key: str, value: Any, ts: int, cutoff: int) -> None:
    """追加一个采样点；同一时间戳重复上报时只保留第一次。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    if not math.isfinite(float(value)):
        return
    points = _series.setdefault(key, [])
    if points and points[-1][0] == ts:
        return
    points.append((ts, round(float(value), 2)))
    drop = 0
    while drop < len(points) and points[drop][0] < cutoff:
        drop += 1
    if drop:
        del points[:drop]
    if len(points) > HISTORY_LIMIT:
        del points[: len(points) - HISTORY_LIMIT]


def record(env_id: int, result: dict[str, Any], *, now_ms: int | None = None) -> None:
    """记一次探测结果：更新最新状态与 CPU / 内存 / 磁盘曲线。"""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cutoff = now - WINDOW_SECONDS * 1000
    with _series_lock:
        _latest[env_id] = {"probe": result, "probedAt": utcnow_iso()}
        for side in SIDES:
            metrics = (result.get(side) or {}).get("metrics") or {}
            if not metrics.get("available"):
                continue
            ts = _stamp_ms(metrics, now)
            _push_locked(f"{env_id}:{side}:cpu", metrics.get("cpuPercent"), ts, cutoff)
            memory = metrics.get("memory") or {}
            _push_locked(f"{env_id}:{side}:mem", memory.get("percent"), ts, cutoff)
            for disk in metrics.get("disks") or []:
                name = str(disk.get("name") or "")
                if name:
                    _push_locked(f"{env_id}:{side}:disk:{name}", disk.get("percent"), ts, cutoff)


def _side_snapshot(env_id: int, side: str, cutoff: int) -> dict[str, Any]:
    prefix = f"{env_id}:{side}:"
    disks: dict[str, dict[str, Any]] = {}
    result: dict[str, Any] = {
        "cpu": {"ts": [], "values": []},
        "memory": {"ts": [], "values": []},
        "disks": disks,
    }
    for key, points in _series.items():
        if not key.startswith(prefix):
            continue
        fresh = [point for point in points if point[0] >= cutoff]
        metric = key[len(prefix):]
        series: dict[str, Any] = {
            "ts": [point[0] for point in fresh],
            "values": [point[1] for point in fresh],
        }
        if metric == "cpu":
            result["cpu"] = series
        elif metric == "mem":
            result["memory"] = series
        elif metric.startswith("disk:"):
            disks[metric[len("disk:"):]] = series
    return result


def snapshot(env_ids: list[int], *, now_ms: int | None = None) -> dict[str, Any]:
    """按环境返回最新探测结果与近 1 小时的曲线。"""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cutoff = now - WINDOW_SECONDS * 1000
    with _series_lock:
        items = []
        for env_id in env_ids:
            latest = _latest.get(env_id) or {}
            items.append({
                "envId": env_id,
                "probedAt": latest.get("probedAt") or "",
                "probe": latest.get("probe"),
                "app": _side_snapshot(env_id, "app", cutoff),
                "db": _side_snapshot(env_id, "db", cutoff),
            })
    return {
        "windowMs": WINDOW_SECONDS * 1000,
        "intervalMs": int(SAMPLE_INTERVAL * 1000),
        "serverTime": utcnow_iso(),
        "list": items,
    }


def _forget_missing(env_ids: set[int]) -> None:
    """环境被删掉后一并清掉它的曲线，避免内存里留孤儿数据。"""
    with _series_lock:
        for env_id in [item for item in _latest if item not in env_ids]:
            _latest.pop(env_id, None)
        for key in [item for item in _series if int(item.split(":", 1)[0]) not in env_ids]:
            _series.pop(key, None)


def _probe_row(row: dict[str, Any]) -> dict[str, Any]:
    return env_probe_service.probe_env(
        app_base_url=str(row.get("app_base_url") or ""),
        app_health_path=str(row.get("app_health_path") or ""),
        db_type=str(row.get("db_type") or ""),
        db_host=str(row.get("db_host") or ""),
        db_port=row.get("db_port"),
        app_metrics_url=str(row.get("app_metrics_url") or ""),
        db_metrics_url=str(row.get("db_metrics_url") or ""),
    )


def sample_once(db_path: Path) -> None:
    """对当前配置的全部环境探测一轮；单套失败不影响其它环境。"""
    conn = get_standalone_conn(db_path)
    try:
        rows = TestEnvRepository(conn).list_all()
    finally:
        conn.close()

    env_ids = {int(row["id"]) for row in rows if row.get("id") is not None}
    for row in rows:
        try:
            record(int(row["id"]), _probe_row(row))
        except Exception:  # noqa: BLE001 - 单套环境异常不能中断整轮采样
            continue
    _forget_missing(env_ids)


def _loop(db_path: Path, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            sample_once(db_path)
        except Exception:  # noqa: BLE001 - 采样线程不能因一次失败退出
            pass
        if stop.wait(SAMPLE_INTERVAL):
            return


def start_sampler(db_path: Path) -> None:
    """每个数据库路径只跑一个采样线程；重复调用不会叠加。"""
    key = str(db_path)
    with _samplers_lock:
        existing = _samplers.get(key)
        if existing is not None and existing[0].is_alive():
            return
        stop = threading.Event()
        thread = threading.Thread(
            target=_loop,
            args=(db_path, stop),
            name="aitp-env-health-sampler",
            daemon=True,
        )
        _samplers[key] = (thread, stop)
        thread.start()
