"""定时整包备份：按计划排队、磁盘空间保护、保留份数与结果留痕。

计划参数存 `sys_config`，服务重启后继续沿用；`backup.auto.next_run_at` 是
唯一的时间锚点，停机错过的时间点在下次启动时会被补跑一次。

真正的打包动作走任务队列（kind=`backup.auto`），与手动备份共用同一套
`backup_service.create_bundle`，避免两套路径产出结构不同的 zip。
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..db import write_guard
from ..db.connection import get_standalone_conn
from ..errors import AppError, ErrorCode
from ..logging_setup import log_event
from ..repositories.sys_config_repo import SysConfigRepository
from . import audit_service, backup_service, job_queue, notification_service

logger = logging.getLogger(__name__)

KIND = "backup.auto"

AUTO_PREFIX = backup_service.AUTO_PREFIX

CONFIG_ENABLED = "backup.auto.enabled"
CONFIG_FREQUENCY = "backup.auto.frequency"
CONFIG_TIME = "backup.auto.time"
CONFIG_WEEKDAY = "backup.auto.weekday"
CONFIG_RETENTION = "backup.auto.retention"
CONFIG_MIN_FREE_MB = "backup.auto.min_free_mb"
CONFIG_NEXT_RUN = "backup.auto.next_run_at"
CONFIG_LAST_RUN = "backup.auto.last_run_at"
CONFIG_LAST_STATUS = "backup.auto.last_status"
CONFIG_LAST_MESSAGE = "backup.auto.last_message"

CONFIG_LABELS: dict[str, str] = {
    CONFIG_ENABLED: "是否按计划自动生成整包备份",
    CONFIG_FREQUENCY: "自动备份频率：daily 每天 / weekly 每周",
    CONFIG_TIME: "自动备份执行时间（本机时间，HH:MM）",
    CONFIG_WEEKDAY: "每周备份的星期几：1=周一 … 7=周日",
    CONFIG_RETENTION: "自动备份保留份数，超出后删除最旧的自动备份",
    CONFIG_MIN_FREE_MB: "磁盘剩余空间低于该值时跳过本次自动备份并告警",
    CONFIG_NEXT_RUN: "下次自动备份时间（服务维护）",
    CONFIG_LAST_RUN: "最近一次自动备份时间（服务维护）",
    CONFIG_LAST_STATUS: "最近一次自动备份结果",
    CONFIG_LAST_MESSAGE: "最近一次自动备份结果说明（服务维护）",
}

FREQUENCY_DAILY = "daily"
FREQUENCY_WEEKLY = "weekly"
FREQUENCIES: dict[str, str] = {
    FREQUENCY_DAILY: "每天",
    FREQUENCY_WEEKLY: "每周",
}
DEFAULT_FREQUENCY = FREQUENCY_DAILY
DEFAULT_TIME = "02:00"
DEFAULT_WEEKDAY = 1
DEFAULT_RETENTION = 7
MIN_RETENTION = 1
MAX_RETENTION = 90
DEFAULT_MIN_FREE_MB = 512
MAX_MIN_FREE_MB = 102400
MAX_DISK_CHECK_MB = 1024 * 1024

STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_LABELS: dict[str, str] = {
    "": "尚未执行",
    STATUS_RUNNING: "执行中",
    STATUS_SUCCESS: "成功",
    STATUS_SKIPPED: "已跳过",
    STATUS_FAILED: "失败",
}

#: 调度轮询间隔（秒）；到点判断本身很轻，不需要更密
SCHEDULER_INTERVAL = 30.0

_TIME_PATTERN = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")

_scheduler_lock = threading.Lock()
_schedulers: dict[str, tuple[threading.Thread, threading.Event]] = {}


def _truthy(raw: Any, default: bool = True) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("false", "0", "no", "off")


def parse_time(text: str) -> tuple[int, int]:
    match = _TIME_PATTERN.fullmatch(text.strip())
    if not match:
        raise AppError(ErrorCode.PARAM_ERROR, "执行时间格式应为 HH:MM，例如 02:00")
    return int(match.group(1)), int(match.group(2))


def _int_value(raw: Any, default: int, low: int, high: int) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return default
    if value < low or value > high:
        return default
    return value


def _parse_stamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _fmt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def compute_next_run(now: datetime, settings: dict[str, Any]) -> datetime:
    """按频率/时间/星期算出下一次执行时刻；结果一定晚于 now。"""
    hour, minute = parse_time(str(settings["time"]))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if str(settings["frequency"]) == FREQUENCY_WEEKLY:
        target = int(settings["weekday"]) - 1
        candidate += timedelta(days=(target - now.weekday()) % 7)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _read_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    repo = SysConfigRepository(conn)
    frequency = str(repo.get_value(CONFIG_FREQUENCY, DEFAULT_FREQUENCY) or DEFAULT_FREQUENCY)
    frequency = frequency.strip().lower()
    if frequency not in FREQUENCIES:
        frequency = DEFAULT_FREQUENCY
    time_text = str(repo.get_value(CONFIG_TIME, DEFAULT_TIME) or DEFAULT_TIME).strip()
    try:
        parse_time(time_text)
    except AppError:
        time_text = DEFAULT_TIME
    return {
        "enabled": _truthy(repo.get_value(CONFIG_ENABLED, "true")),
        "frequency": frequency,
        "time": time_text,
        "weekday": _int_value(repo.get_value(CONFIG_WEEKDAY), DEFAULT_WEEKDAY, 1, 7),
        "retention": _int_value(
            repo.get_value(CONFIG_RETENTION), DEFAULT_RETENTION, MIN_RETENTION, MAX_RETENTION
        ),
        "min_free_mb": _int_value(
            repo.get_value(CONFIG_MIN_FREE_MB),
            DEFAULT_MIN_FREE_MB,
            0,
            MAX_MIN_FREE_MB,
        ),
        "next_run_at": str(repo.get_value(CONFIG_NEXT_RUN, "") or ""),
        "last_run_at": str(repo.get_value(CONFIG_LAST_RUN, "") or ""),
        "last_status": str(repo.get_value(CONFIG_LAST_STATUS, "") or ""),
        "last_message": str(repo.get_value(CONFIG_LAST_MESSAGE, "") or ""),
    }


def _free_bytes(directory: Path) -> int:
    try:
        return int(shutil.disk_usage(str(directory)).free)
    except OSError:
        return -1


def _set(conn: sqlite3.Connection, key: str, value: str, value_type: str = "string") -> None:
    SysConfigRepository(conn).set_value(
        key,
        value,
        value_type=value_type,
        description=CONFIG_LABELS.get(key, ""),
        is_system=1,
    )


def _record(
    conn: sqlite3.Connection,
    *,
    status: str,
    message: str,
    run_at: datetime,
    next_run: datetime | None,
) -> None:
    _set(conn, CONFIG_LAST_RUN, _fmt(run_at))
    _set(conn, CONFIG_LAST_STATUS, status)
    _set(conn, CONFIG_LAST_MESSAGE, message[:500])
    if next_run is not None:
        _set(conn, CONFIG_NEXT_RUN, _fmt(next_run))


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    settings = _read_settings(conn)
    now = datetime.now()
    next_run = _parse_stamp(str(settings["next_run_at"]))
    if next_run is None and settings["enabled"]:
        next_run = compute_next_run(now, settings)
    auto = backup_service.list_auto_bundles()
    disk_free = _free_bytes(backup_service.backup_dir())
    last_status = str(settings["last_status"])
    return {
        "enabled": bool(settings["enabled"]),
        "frequency": settings["frequency"],
        "frequencyLabel": FREQUENCIES.get(str(settings["frequency"]), str(settings["frequency"])),
        "time": settings["time"],
        "weekday": int(settings["weekday"]),
        "retention": int(settings["retention"]),
        "minFreeMb": int(settings["min_free_mb"]),
        "nextRunAt": _fmt(next_run) if next_run is not None else "",
        "lastRunAt": settings["last_run_at"],
        "lastStatus": last_status,
        "lastStatusLabel": STATUS_LABELS.get(last_status, last_status),
        "lastMessage": settings["last_message"],
        "autoCount": len(auto),
        "autoBytes": sum(int(item["sizeBytes"]) for item in auto),
        "diskFreeBytes": disk_free,
    }


def _bool_field(raw: Any, label: str) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    raise AppError(ErrorCode.PARAM_ERROR, f"{label} 必须是 true 或 false")


def _int_field(raw: Any, label: str, low: int, high: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.PARAM_ERROR, f"{label} 必须是整数") from exc
    if value < low or value > high:
        raise AppError(ErrorCode.PARAM_ERROR, f"{label} 应在 {low}~{high} 之间")
    return value


def validate_settings(data: dict[str, Any]) -> dict[str, Any]:
    frequency = str(data.get("frequency") or DEFAULT_FREQUENCY).strip().lower()
    if frequency not in FREQUENCIES:
        raise AppError(ErrorCode.PARAM_ERROR, "备份频率只能是 daily 或 weekly")
    time_text = str(data.get("time") or DEFAULT_TIME).strip()
    parse_time(time_text)
    return {
        "enabled": _bool_field(data.get("enabled"), "启用自动备份"),
        "frequency": frequency,
        "time": time_text,
        "weekday": _int_field(data.get("weekday", DEFAULT_WEEKDAY), "星期", 1, 7),
        "retention": _int_field(
            data.get("retention", DEFAULT_RETENTION), "保留份数", MIN_RETENTION, MAX_RETENTION
        ),
        "min_free_mb": _int_field(
            data.get("minFreeMb", DEFAULT_MIN_FREE_MB), "磁盘保留空间(MB)", 0, MAX_MIN_FREE_MB
        ),
    }


def save_settings(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """保存计划；只有频率/时间/星期/开关变化时才重置下次执行时间。"""
    old = _read_settings(conn)
    now = now or datetime.now()
    _set(conn, CONFIG_ENABLED, "true" if values["enabled"] else "false", "bool")
    _set(conn, CONFIG_FREQUENCY, str(values["frequency"]))
    _set(conn, CONFIG_TIME, str(values["time"]))
    _set(conn, CONFIG_WEEKDAY, str(values["weekday"]), "int")
    _set(conn, CONFIG_RETENTION, str(values["retention"]), "int")
    _set(conn, CONFIG_MIN_FREE_MB, str(values["min_free_mb"]), "int")
    schedule_changed = any(
        values[key] != old[key] for key in ("enabled", "frequency", "time", "weekday")
    )
    if schedule_changed:
        if values["enabled"]:
            _set(conn, CONFIG_NEXT_RUN, _fmt(compute_next_run(now, values)))
        else:
            _set(conn, CONFIG_NEXT_RUN, "")
    return status(conn)


def _submit(conn: sqlite3.Connection, *, trigger: str, now: datetime) -> dict[str, Any]:
    task = job_queue.submit(
        conn,
        kind=KIND,
        title="定时整包备份" if trigger == "schedule" else "手动触发整包备份",
        payload={"trigger": trigger, "requestedAt": _fmt(now)},
        priority=110,
    )
    return task


def run_due_once(db_path: Path, *, now: datetime | None = None) -> dict[str, Any] | None:
    """到点就排一次备份；未到点、已关闭或磁盘不足时返回可读结果。

    首次运行没有 next_run_at 时只写入下次时间，避免一启动就打包。
    """
    current = now or datetime.now()
    conn = get_standalone_conn(db_path)
    try:
        settings = _read_settings(conn)
        if not settings["enabled"]:
            return None
        stamp = _parse_stamp(str(settings["next_run_at"]))
        if stamp is None:
            _set(conn, CONFIG_NEXT_RUN, _fmt(compute_next_run(current, settings)))
            conn.commit()
            return None
        if stamp > current:
            return None

        disk_free = _free_bytes(db_path.parent)
        required = int(settings["min_free_mb"]) * 1024 * 1024
        if 0 <= disk_free < required:
            next_run = compute_next_run(current, settings)
            reason = (
                f"磁盘剩余 {disk_free // (1024 * 1024)} MB，低于保留阈值 "
                f"{settings['min_free_mb']} MB，本次自动备份已跳过"
            )
            _record(
                conn, status=STATUS_SKIPPED, message=reason,
                run_at=current, next_run=next_run,
            )
            audit_service.record(
                conn,
                audit_service.AUDIT_ACTION_BACKUP,
                audit_service.RESOURCE_DB_BACKUP,
                None,
                "定时整包备份",
                {"skipped": True, "reason": reason},
                actor="scheduler",
                result="skipped",
            )
            notification_service.notify_backup_skipped(conn, reason)
            conn.commit()
            return {"skipped": True, "reason": reason, "nextRunAt": _fmt(next_run)}

        next_run = compute_next_run(current, settings)
        _record(
            conn,
            status=STATUS_RUNNING,
            message="定时任务已排队，等待执行",
            run_at=current,
            next_run=next_run,
        )
        # 先落状态再排队：worker 可能瞬间跑完，后写 running 会覆盖成功结果
        conn.commit()
        try:
            task = _submit(conn, trigger="schedule", now=current)
        except Exception as exc:
            _record(
                conn,
                status=STATUS_FAILED,
                message=f"定时备份任务提交失败：{exc}",
                run_at=current,
                next_run=next_run,
            )
            conn.commit()
            raise
        return {"taskId": task.get("id"), "nextRunAt": _fmt(next_run)}
    finally:
        conn.close()


def submit_now(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, Any]:
    """手动执行一次自动备份；保留策略与计划任务一致。"""
    current = now or datetime.now()
    _set(conn, CONFIG_LAST_STATUS, STATUS_RUNNING)
    _set(conn, CONFIG_LAST_MESSAGE, "手动触发的整包备份已排队")
    conn.commit()
    try:
        return _submit(conn, trigger="manual", now=current)
    except Exception as exc:
        _set(conn, CONFIG_LAST_STATUS, STATUS_FAILED)
        _set(conn, CONFIG_LAST_MESSAGE, f"手动备份任务提交失败：{exc}")
        conn.commit()
        raise


def run_auto_backup(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    payload: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    """队列处理器：打包 → 清理超量自动备份 → 记录结果与通知。"""
    settings = _read_settings(conn)
    current = datetime.now()
    disk_free = _free_bytes(backup_service.backup_dir())
    required = int(settings["min_free_mb"]) * 1024 * 1024
    if 0 <= disk_free < required:
        reason = (
            f"磁盘剩余 {disk_free // (1024 * 1024)} MB，低于保留阈值 "
            f"{settings['min_free_mb']} MB，本次自动备份已跳过"
        )
        _record(
            conn, status=STATUS_SKIPPED, message=reason,
            run_at=current, next_run=compute_next_run(current, settings),
        )
        audit_service.record(
            conn,
            audit_service.AUDIT_ACTION_BACKUP,
            audit_service.RESOURCE_DB_BACKUP,
            None,
            "定时整包备份",
            {"skipped": True, "reason": reason},
            actor="scheduler",
            result="skipped",
        )
        try:
            notification_service.notify_backup_skipped(conn, reason)
        except Exception:  # noqa: BLE001 - 通知是附加动作
            conn.rollback()
        conn.commit()
        return {"skipped": True, "reason": reason}

    try:
        info = backup_service.create_bundle(conn, prefix=AUTO_PREFIX)
        removed = backup_service.prune_auto(int(settings["retention"]))
        next_run = compute_next_run(current, settings)
        _record(
            conn,
            status=STATUS_SUCCESS,
            message=f"自动备份成功：{info['name']}（{info.get('files', 0)} 个文件）",
            run_at=current,
            next_run=next_run,
        )
        audit_service.record(
            conn,
            audit_service.AUDIT_ACTION_BACKUP,
            audit_service.RESOURCE_DB_BACKUP,
            None,
            str(info["name"]),
            {
                "auto": True,
                "trigger": payload.get("trigger") or "schedule",
                "sizeBytes": info["sizeBytes"],
                "files": info.get("files"),
                "retentionRemoved": removed,
            },
            actor="scheduler",
        )
        conn.commit()
        try:
            notification_service.notify_backup_done(conn, info, removed)
            conn.commit()
        except Exception:  # noqa: BLE001 - 通知是附加动作
            conn.rollback()
        return {
            "name": info["name"],
            "sizeBytes": info["sizeBytes"],
            "files": info.get("files"),
            "retentionRemoved": removed,
            "nextRunAt": _fmt(next_run),
        }
    except Exception as exc:
        conn.rollback()
        message = f"自动备份失败：{type(exc).__name__}: {exc}"
        _record(
            conn, status=STATUS_FAILED, message=message,
            run_at=current, next_run=compute_next_run(current, settings),
        )
        audit_service.record(
            conn,
            audit_service.AUDIT_ACTION_BACKUP,
            audit_service.RESOURCE_DB_BACKUP,
            None,
            "定时整包备份",
            {"auto": True, "error": message[:500]},
            actor="scheduler",
            result="failed",
        )
        try:
            notification_service.notify_backup_failed(conn, message)
        except Exception:  # noqa: BLE001 - 通知是附加动作
            conn.rollback()
        conn.commit()
        raise


def _scheduler_loop(db_path: Path, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            run_due_once(db_path)
        except Exception as exc:  # noqa: BLE001 - 调度线程不能因一次失败退出，但必须留痕
            log_event(logger, "backup_scheduler_failed", level=logging.WARNING,
                      traceId=write_guard.log_trace_id(), db=str(db_path.name),
                      error=f"{type(exc).__name__}: {exc}",
                      impact="本轮自动备份跳过，下一轮照常")
        if stop.wait(SCHEDULER_INTERVAL):
            return


def start_scheduler(db_path: Path) -> None:
    """每个数据库路径只跑一个调度线程；重复调用不会叠加。"""
    key = str(db_path)
    with _scheduler_lock:
        existing = _schedulers.get(key)
        if existing is not None and existing[0].is_alive():
            return
        stop = threading.Event()
        thread = threading.Thread(
            target=_scheduler_loop,
            args=(db_path, stop),
            name="aitp-backup-scheduler",
            daemon=True,
        )
        _schedulers[key] = (thread, stop)
        thread.start()


def stop_scheduler(db_path: Path | None = None) -> None:
    with _scheduler_lock:
        keys = [str(db_path)] if db_path is not None else list(_schedulers)
        for key in keys:
            item = _schedulers.pop(key, None)
            if item is not None:
                item[1].set()


job_queue.register(KIND, run_auto_backup)
