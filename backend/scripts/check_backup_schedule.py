"""GAP-13 定时备份与保留策略验收脚本。

覆盖：
- 下次执行时间按「每天 / 每周 + 时间 + 星期」计算，非法参数被拒；
- 计划保存在 sys_config，重新读取后不丢（模拟重启）；
- 到点后真的通过任务队列跑一次整包备份（真 worker），产出可恢复的 zip；
- 保留份数只清理最旧的自动整包，不碰手动整包与恢复前整包；
- 磁盘剩余不足时跳过、写可读原因与审计，不产生新备份；
- 手动触发一次复用同一套保留策略；
- 调度线程按数据库路径去重，不会叠加。

    python backend/scripts/check_backup_schedule.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.connection import get_standalone_conn  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.repositories.sys_config_repo import SysConfigRepository  # noqa: E402
from aitp.services import backup_schedule, backup_service  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

_passed = 0
_failed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    _failures.append(name)
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    def __init__(self, app: Flask) -> None:
        self.app = app
        self.client = app.test_client()

    def call(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload, **kwargs)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> Any:
        _, body = self.call(method, path, payload, **kwargs)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")

    def code(self, method: str, path: str, payload: JsonDict) -> Any:
        _, body = self.call(method, path, payload)
        return body.get("code")


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_backup.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check_backup.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def set_config(db_path: pathlib.Path, key: str, value: str, value_type: str = "string") -> None:
    conn = get_standalone_conn(db_path)
    try:
        SysConfigRepository(conn).set_value(key, value, value_type=value_type)
        conn.commit()
    finally:
        conn.close()


def get_config(db_path: pathlib.Path, key: str) -> str:
    conn = get_standalone_conn(db_path)
    try:
        return str(SysConfigRepository(conn).get_value(key, "") or "")
    finally:
        conn.close()


def set_next_run_past(db_path: pathlib.Path) -> None:
    past = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    set_config(db_path, backup_schedule.CONFIG_NEXT_RUN, past)


def wait_task(api: Api, task_id: int, timeout: float = 20.0) -> JsonDict:
    deadline = time.time() + timeout
    task: JsonDict = {}
    while time.time() < deadline:
        task = cast(JsonDict, api.data("GET", f"/api/v1/tasks/{task_id}"))
        if task.get("status") in ("success", "failed", "cancelled"):
            return task
        time.sleep(0.1)
    return task


def auto_files(backup_dir: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path for path in backup_dir.glob("auto-*.zip")
        if backup_service.BUNDLE_NAME_PATTERN.match(path.name)
    )


def last_audit(conn: sqlite3.Connection, action: str = "backup") -> JsonDict:
    row = conn.execute(
        "SELECT * FROM sys_audit_log WHERE action = ? ORDER BY id DESC LIMIT 1", (action,)
    ).fetchone()
    return dict(row) if row else {}


def check_units() -> None:
    monday = datetime(2026, 11, 2, 0, 0)
    settings = {
        "enabled": True, "frequency": "daily", "time": "02:00",
        "weekday": 1, "retention": 7, "min_free_mb": 512,
    }
    same_day = backup_schedule.compute_next_run(monday.replace(hour=1), settings)
    check("每天计划：未到点走当天", same_day == monday.replace(hour=2), str(same_day))
    next_day = backup_schedule.compute_next_run(monday.replace(hour=3), settings)
    check("每天计划：过点顺延到次日", next_day == monday + timedelta(days=1, hours=2),
          str(next_day))

    weekly = dict(settings, frequency="weekly", weekday=3, time="03:30")
    wednesday = backup_schedule.compute_next_run(monday, weekly)
    check("每周计划：算到指定星期", wednesday == monday + timedelta(days=2, hours=3, minutes=30),
          str(wednesday))
    after = backup_schedule.compute_next_run(wednesday.replace(hour=4), weekly)
    check("每周计划：过点顺延一周", after == wednesday + timedelta(days=7), str(after))

    try:
        backup_schedule.parse_time("25:00")
        check("非法时间被拒绝", False, "25:00 未被拒绝")
    except AppError as exc:
        check("非法时间被拒绝", exc.code == ErrorCode.PARAM_ERROR, str(exc))

    for payload, label in (
        ({"enabled": True, "frequency": "monthly", "time": "02:00"}, "非法频率"),
        ({"enabled": True, "frequency": "daily", "time": "2:00"}, "非法时间格式"),
        ({"enabled": True, "frequency": "weekly", "time": "02:00", "weekday": 8}, "非法星期"),
        ({"enabled": True, "frequency": "daily", "time": "02:00", "retention": 0}, "非法保留份数"),
        ({"enabled": "maybe", "frequency": "daily", "time": "02:00"}, "非法开关值"),
    ):
        try:
            backup_schedule.validate_settings(payload)
            check(f"{label}被拒绝", False, "没有抛出参数错误")
        except AppError as exc:
            check(f"{label}被拒绝", exc.code == ErrorCode.PARAM_ERROR, str(exc))


def check_config_api(api: Api, db_path: pathlib.Path) -> None:
    plan = api.data("GET", "/api/v1/settings/backups")["auto"]
    check("自动备份默认开启", plan.get("enabled") is True, str(plan))
    check("默认每天执行", plan.get("frequency") == "daily", str(plan))
    check("默认执行时间 02:00", plan.get("time") == "02:00", str(plan))
    check("默认保留 7 份", plan.get("retention") == 7, str(plan))
    check("默认磁盘阈值 512MB", plan.get("minFreeMb") == 512, str(plan))
    check("默认给出下次执行时间", bool(plan.get("nextRunAt")), str(plan))

    saved = api.data("PUT", "/api/v1/settings/backups/auto", {
        "enabled": True, "frequency": "daily", "time": "03:15",
        "weekday": 1, "retention": 5, "minFreeMb": 256,
    })
    check("保存计划后回显新时间", saved.get("time") == "03:15", str(saved))
    check("保存计划后回显保留份数", saved.get("retention") == 5, str(saved))
    check("保存计划后回显磁盘阈值", saved.get("minFreeMb") == 256, str(saved))
    next_run = datetime.strptime(str(saved.get("nextRunAt")), "%Y-%m-%d %H:%M:%S")
    check("保存后下次执行时间在未来", next_run > datetime.now(), str(saved.get("nextRunAt")))
    check("每周计划带星期说明", saved.get("frequencyLabel") == "每天", str(saved))

    check("非法时间返回参数错误",
          api.code("PUT", "/api/v1/settings/backups/auto", {
              "enabled": True, "frequency": "daily", "time": "25:00",
          }) == ErrorCode.PARAM_ERROR)
    check("非法保留份数返回参数错误",
          api.code("PUT", "/api/v1/settings/backups/auto", {
              "enabled": True, "frequency": "daily", "time": "02:00", "retention": 999,
          }) == ErrorCode.PARAM_ERROR)

    # 模拟重启：换一条独立连接重新读取，计划参数仍在
    conn = get_standalone_conn(db_path)
    try:
        with api.app.app_context():
            restored = backup_schedule.status(conn)
    finally:
        conn.close()
    check("重启后频率仍在", restored.get("frequency") == "daily", str(restored))
    check("重启后时间仍在", restored.get("time") == "03:15", str(restored))
    check("重启后保留份数仍在", restored.get("retention") == 5, str(restored))
    check("重启后下次执行时间仍在", bool(restored.get("nextRunAt")), str(restored))

    closed = api.data("PUT", "/api/v1/settings/backups/auto", {
        "enabled": False, "frequency": "daily", "time": "03:15",
        "weekday": 1, "retention": 5, "minFreeMb": 256,
    })
    check("关闭后清空下次执行时间", closed.get("nextRunAt") == "", str(closed))
    check("关闭状态可回显", closed.get("enabled") is False, str(closed))

    api.data("PUT", "/api/v1/settings/backups/auto", {
        "enabled": True, "frequency": "daily", "time": "03:15",
        "weekday": 1, "retention": 5, "minFreeMb": 256,
    })


def check_due_run(api: Api, db_path: pathlib.Path) -> None:
    backup_dir = db_path.parent / backup_service.BACKUP_SUBDIR
    before = auto_files(backup_dir)
    set_next_run_past(db_path)
    due = backup_schedule.run_due_once(db_path)
    check("到点后调度器提交任务", bool(due and due.get("taskId")), str(due))
    task = wait_task(api, int((due or {}).get("taskId") or 0))
    check("定时备份任务执行成功", task.get("status") == "success", str(task))
    check("任务类型标注为定时整包备份", task.get("kindLabel") == "定时整包备份", str(task))
    check("任务结果带备份文件名", str((task.get("result") or {}).get("name", "")).startswith("auto-"),
          str(task.get("result")))

    after = auto_files(backup_dir)
    check("自动备份文件已生成", len(after) == len(before) + 1, f"{len(before)} → {len(after)}")
    newest = after[-1]
    check("自动备份文件非空", newest.stat().st_size > 0, str(newest.stat().st_size))
    with zipfile.ZipFile(newest) as zf:
        names = set(zf.namelist())
    check("整包里带数据库快照", "aitp.db" in names, str(sorted(names)[:5]))
    check("整包里带清单文件", "manifest.json" in names, str(sorted(names)[:5]))

    plan = api.data("GET", "/api/v1/settings/backups")["auto"]
    check("计划回显最近执行成功", plan.get("lastStatus") == "success", str(plan))
    check("计划回显最近执行时间", bool(plan.get("lastRunAt")), str(plan))
    check("成功后下次执行时间在未来",
          datetime.strptime(str(plan.get("nextRunAt")), "%Y-%m-%d %H:%M:%S") > datetime.now(),
          str(plan.get("nextRunAt")))
    check("自动备份计数含新文件", int(plan.get("autoCount") or 0) >= 1, str(plan))
    check("自动备份占用字节数已统计", int(plan.get("autoBytes") or 0) > 0, str(plan))

    conn = get_standalone_conn(db_path)
    try:
        entry = last_audit(conn)
    finally:
        conn.close()
    detail = json.loads(str(entry.get("detail_json") or "{}"))
    check("自动备份写审计日志", entry.get("action") == "backup", str(entry.get("action")))
    check("审计标记为自动备份", detail.get("auto") is True, str(detail))

    bundles = api.data("GET", "/api/v1/settings/backups")["bundles"]
    listed = [item for item in bundles if item.get("name") == newest.name]
    check("备份列表能识别自动整包", bool(listed and listed[0].get("isAuto")), str(listed[:1]))


def check_retention(api: Api, db_path: pathlib.Path) -> None:
    backup_dir = db_path.parent / backup_service.BACKUP_SUBDIR
    manual = api.data("POST", "/api/v1/settings/backups/bundle")
    manual_path = backup_dir / str(manual["name"])
    safety_dummy = backup_dir / "pre-bundle-20200101-000001.zip"
    safety_dummy.write_bytes(b"")
    for stale in auto_files(backup_dir):
        stale.unlink()
    for index in range(1, 6):
        (backup_dir / f"auto-20200101-00000{index}.zip").write_bytes(b"")

    api.data("PUT", "/api/v1/settings/backups/auto", {
        "enabled": True, "frequency": "daily", "time": "03:15",
        "weekday": 1, "retention": 3, "minFreeMb": 0,
    })
    set_next_run_past(db_path)
    due = backup_schedule.run_due_once(db_path)
    task = wait_task(api, int((due or {}).get("taskId") or 0))
    check("保留策略下任务成功", task.get("status") == "success", str(task))

    remaining = [path.name for path in auto_files(backup_dir)]
    check("保留份数生效", len(remaining) == 3, str(remaining))
    check("保留的是最新的自动备份",
          "auto-20200101-000004.zip" in remaining and "auto-20200101-000005.zip" in remaining,
          str(remaining))
    check("最旧的自动备份已清理", "auto-20200101-000001.zip" not in remaining, str(remaining))
    removed = (task.get("result") or {}).get("retentionRemoved") or []
    check("任务结果记录被清理的文件", "auto-20200101-000001.zip" in removed, str(removed))
    check("手动整包不受保留策略影响", manual_path.is_file(), str(manual_path))
    check("恢复前整包不受保留策略影响", safety_dummy.is_file(), str(safety_dummy))


def check_disk_guard(api: Api, db_path: pathlib.Path) -> None:
    backup_dir = db_path.parent / backup_service.BACKUP_SUBDIR
    api.data("PUT", "/api/v1/settings/backups/auto", {
        "enabled": True, "frequency": "daily", "time": "03:15",
        "weekday": 1, "retention": 3, "minFreeMb": 1024,
    })
    original = backup_schedule._free_bytes
    backup_schedule._free_bytes = lambda _path: 10 * 1024 * 1024  # type: ignore[assignment]
    try:
        before_files = len(auto_files(backup_dir))
        tasks_before = int(api.data("GET", "/api/v1/tasks?pageSize=100")["total"])
        set_next_run_past(db_path)
        due = backup_schedule.run_due_once(db_path)
        check("磁盘不足时调度器返回跳过", bool(due and due.get("skipped")), str(due))
        check("跳过原因可读", "磁盘剩余" in str((due or {}).get("reason")) and
              "跳过" in str((due or {}).get("reason")), str(due))
        check("跳过时不提交备份任务",
              int(api.data("GET", "/api/v1/tasks?pageSize=100")["total"]) == tasks_before)
        check("跳过时不生成新备份", len(auto_files(backup_dir)) == before_files)

        plan = api.data("GET", "/api/v1/settings/backups")["auto"]
        check("最近结果为已跳过", plan.get("lastStatus") == "skipped", str(plan))
        check("跳过原因写入计划回显", "磁盘剩余" in str(plan.get("lastMessage")), str(plan))
        check("跳过后下次执行时间仍在未来",
              datetime.strptime(str(plan.get("nextRunAt")), "%Y-%m-%d %H:%M:%S") > datetime.now(),
              str(plan.get("nextRunAt")))

        conn = get_standalone_conn(db_path)
        try:
            entry = last_audit(conn)
            # 直接调用队列处理器，确认 worker 侧也会二次检查磁盘
            with api.app.app_context():
                result = backup_schedule.run_auto_backup(conn, {}, {}, db_path)
            conn.commit()
        finally:
            conn.close()
        check("跳过写审计结果", entry.get("result") == "skipped", str(entry.get("result")))
        check("队列处理器同样会跳过", bool(result.get("skipped")), str(result))
        check("队列处理器跳过后不产生新备份", len(auto_files(backup_dir)) == before_files)
    finally:
        backup_schedule._free_bytes = original

    api.data("PUT", "/api/v1/settings/backups/auto", {
        "enabled": True, "frequency": "daily", "time": "03:15",
        "weekday": 1, "retention": 3, "minFreeMb": 0,
    })


def check_manual_trigger(api: Api, db_path: pathlib.Path) -> None:
    backup_dir = db_path.parent / backup_service.BACKUP_SUBDIR
    before_names = {path.name for path in auto_files(backup_dir)}
    plan_before = api.data("GET", "/api/v1/settings/backups")["auto"]
    task = api.data("POST", "/api/v1/settings/backups/auto/run", {})
    finished = wait_task(api, int(task.get("id") or 0))
    check("手动触发任务执行成功", finished.get("status") == "success", str(finished))
    created = str((finished.get("result") or {}).get("name") or "")
    check("手动触发会生成自动整包",
          created.startswith("auto-") and (backup_dir / created).is_file()
          and created not in before_names,
          str(finished.get("result")))
    plan_after = api.data("GET", "/api/v1/settings/backups")["auto"]
    check("手动触发不改变下次计划时间",
          plan_after.get("nextRunAt") == plan_before.get("nextRunAt"),
          f"{plan_before.get('nextRunAt')} → {plan_after.get('nextRunAt')}")
    check("手动触发仍应用保留份数", len(auto_files(backup_dir)) == 3,
          str([p.name for p in auto_files(backup_dir)]))


def check_scheduler_singleton(db_path: pathlib.Path) -> None:
    backup_schedule.stop_scheduler(db_path)
    backup_schedule.start_scheduler(db_path)
    backup_schedule.start_scheduler(db_path)
    with backup_schedule._scheduler_lock:
        entries = [key for key in backup_schedule._schedulers if key == str(db_path)]
    check("同一数据库只保留一个调度线程", len(entries) == 1, str(entries))
    backup_schedule.stop_scheduler(db_path)
    with backup_schedule._scheduler_lock:
        check("调度线程可停止", str(db_path) not in backup_schedule._schedulers)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_backup_"))
    db_path = workdir / "check_backup.db"
    backup_dir = workdir / backup_service.BACKUP_SUBDIR
    app = build_app(workdir)
    api = Api(app)
    backup_schedule.stop_scheduler(db_path)
    try:
        check_units()
        check_config_api(api, db_path)
        check_due_run(api, db_path)
        check_retention(api, db_path)
        check_disk_guard(api, db_path)
        check_manual_trigger(api, db_path)
        check_scheduler_singleton(db_path)
    finally:
        backup_schedule.stop_scheduler(db_path)
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
