"""P2 定时调度 API：CRUD + 立即触发一次。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, Response

from ..common.validators import check_choice, optional_int, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.schedule_repo import TestScheduleRepository
from ..services import job_queue, test_schedule_service
from .helpers import body, bool_arg, commit, db, keyword_of, page_args, page_payload, path_int, query_filters, require_project

bp = Blueprint("schedules", __name__)

#: 调度间隔上限（分钟）= 1 天。再长没有意义（等价于「每天跑一次」），再短会被钳到 1 分钟造成调度风暴
SCHEDULE_MAX_INTERVAL = 1440


def _interval_of(raw: Any) -> int:
    """调度间隔：缺省 60 分钟，钳到 [1, SCHEDULE_MAX_INTERVAL]。"""
    value = optional_int(raw, "intervalMinutes") or 60
    return max(1, min(value, SCHEDULE_MAX_INTERVAL))


def _public(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("id"), "scheduleNo": r.get("schedule_no") or "",
        "projectId": r.get("project_id"), "name": r.get("name"),
        "targetType": r.get("target_type"), "targetId": r.get("target_id"),
        "intervalMinutes": r.get("interval_minutes"), "enabled": bool(r.get("enabled")),
        "nextRunAt": r.get("next_run_at"), "lastRunAt": r.get("last_run_at"),
        "createdAt": r.get("created_at"),
    }


@bp.get("")
def list_schedules() -> Response:
    conn = db()
    page_num, page_size = page_args()
    page = TestScheduleRepository(conn).list(query_filters({"projectId": "project_id"}),
                                             keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [_public(r) for r in payload["list"]]
    return ok(payload)


@bp.post("")
def create_schedule() -> Response:
    conn = db()
    data = body()
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    name = require_str(data.get("name"), "name", 100)
    target_type = check_choice(str(data.get("targetType") or "scenario"), ("scenario", "script"), "targetType", "scenario") or "scenario"
    target_id = require_int(data.get("targetId"), "targetId")
    interval = _interval_of(data.get("intervalMinutes"))
    now = datetime.now(timezone.utc)
    repo = TestScheduleRepository(conn)
    new_id = repo.insert({
        "schedule_no": repo.next_schedule_no(),
        "project_id": project_id, "name": name, "target_type": target_type,
        "target_id": target_id, "interval_minutes": interval,
        "enabled": 1 if bool_arg(data.get("enabled"), True) else 0,
        "next_run_at": (now + timedelta(minutes=interval)).isoformat(),
    })
    commit(conn)
    return ok(_public(repo.get(new_id) or {}))


@bp.put("/<pk>")
def update_schedule(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = TestScheduleRepository(conn)
    if repo.get(target) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "调度不存在")
    data = body()
    payload: dict[str, Any] = {}
    if "name" in data:
        payload["name"] = require_str(data["name"], "name", 100)
    if "intervalMinutes" in data:
        payload["interval_minutes"] = _interval_of(data["intervalMinutes"])
    if "enabled" in data:
        payload["enabled"] = 1 if bool_arg(data["enabled"]) else 0
    if "targetType" in data:
        payload["target_type"] = check_choice(str(data["targetType"]), ("scenario", "script"), "targetType", "scenario") or "scenario"
    if "targetId" in data:
        payload["target_id"] = require_int(data["targetId"], "targetId")
    repo.update(target, payload)
    commit(conn)
    return ok(_public(repo.get(target) or {}))


@bp.delete("/<pk>")
def delete_schedule(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = TestScheduleRepository(conn)
    if repo.get(target) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "调度不存在")
    repo.soft_delete(target)
    commit(conn)
    return ok({"deleted": True})


@bp.post("/<pk>/trigger")
def trigger_schedule(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = TestScheduleRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "调度不存在")
    if str(row.get("target_type")) == "scenario":
        task = job_queue.submit(conn, kind=test_schedule_service.KIND_SCENARIO,
                                title=f"手动场景 · {row.get('name')}",
                                payload={"scenarioId": int(row["target_id"])}, priority=70)
    else:
        task = job_queue.submit(conn, kind="automation.run",
                                title=f"手动脚本 · {row.get('name')}",
                                payload={"scriptId": int(row["target_id"])}, priority=70)
    commit(conn)
    return ok({"taskId": task.get("id")})