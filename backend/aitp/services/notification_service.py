"""Webhook 通知：事件投递、重试与发送留痕。

投递走持久化任务队列，网络失败按 ``max_attempts`` 重试，每次尝试都写
``sys_notification_log``；通知失败只影响通知本身，不改变轮次/门禁结果。
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from typing import Any

from ..common.time_utils import utcnow_iso
from ..repositories.ci_repo import (
    NOTIFICATION_FAILED,
    NOTIFICATION_PENDING,
    NOTIFICATION_SUCCESS,
    CiTokenRepository,
    NotificationChannelRepository,
    NotificationLogRepository,
    channel_summary,
)
from ..repositories.test_run_repo import TestRunRepository, run_summary
from . import job_queue

EVENT_RUN_COMPLETED = "run.completed"
EVENT_GATE_BLOCKED = "gate.failed"
EVENT_AUTOMATION_FAILED = "automation.failed"
EVENT_BACKUP_FAILED = "backup.failed"
EVENT_BACKUP_DONE = "backup.done"
EVENT_BACKUP_SKIPPED = "backup.skipped"
EVENT_TEST = "test.ping"

EVENT_LABELS: dict[str, str] = {
    EVENT_RUN_COMPLETED: "测试轮次完成",
    EVENT_GATE_BLOCKED: "发布门禁未通过",
    EVENT_AUTOMATION_FAILED: "自动化执行失败",
    EVENT_BACKUP_FAILED: "备份失败",
    EVENT_BACKUP_DONE: "自动备份完成",
    EVENT_BACKUP_SKIPPED: "自动备份跳过",
    EVENT_TEST: "测试通知",
}

#: 允许订阅的业务事件（测试通知只能手动触发）
SUBSCRIBABLE_EVENTS: tuple[str, ...] = (
    EVENT_RUN_COMPLETED,
    EVENT_GATE_BLOCKED,
    EVENT_AUTOMATION_FAILED,
    EVENT_BACKUP_FAILED,
    EVENT_BACKUP_DONE,
    EVENT_BACKUP_SKIPPED,
)

KIND = "notification.webhook"
MAX_RESPONSE_CHARS = 500
DEFAULT_TIMEOUT = 10

job_queue.KIND_LABELS[KIND] = "Webhook 通知"


def base_url() -> str:
    host = os.environ.get("AITP_HOST", "127.0.0.1")
    port = os.environ.get("AITP_PORT", "8760")
    return f"http://{host}:{port}"


def _project_name(conn: sqlite3.Connection, project_id: int | None) -> str:
    if not project_id:
        return ""
    row = conn.execute(
        "SELECT name FROM biz_project WHERE id = ? AND is_deleted = 0", (int(project_id),)
    ).fetchone()
    return str(row["name"]) if row else ""


def _link(path: str) -> str:
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base_url() + (path if path.startswith("/") else f"/{path}")


def build_payload(
    conn: sqlite3.Connection,
    *,
    event: str,
    title: str,
    project_id: int | None,
    data: dict[str, Any],
    link: str = "",
) -> dict[str, Any]:
    return {
        "event": event,
        "eventLabel": EVENT_LABELS.get(event, event),
        "title": title,
        "projectId": project_id,
        "projectName": _project_name(conn, project_id),
        "occurredAt": utcnow_iso(),
        "link": _link(link),
        "data": data,
    }


def matching_channels(
    conn: sqlite3.Connection, event: str, project_id: int | None
) -> list[dict[str, Any]]:
    """全局渠道 + 当前项目渠道，且订阅了该事件。"""
    matched: list[dict[str, Any]] = []
    for row in NotificationChannelRepository(conn).list_enabled():
        channel = channel_summary(row)
        if event not in channel["events"]:
            continue
        channel_project = channel.get("projectId")
        if channel_project not in (None, "") and int(channel_project) != int(project_id or 0):
            continue
        matched.append(channel)
    return matched


def notify(
    conn: sqlite3.Connection,
    *,
    event: str,
    title: str,
    project_id: int | None,
    data: dict[str, Any],
    link: str = "",
) -> list[dict[str, Any]]:
    """给所有匹配渠道各排一次投递；返回创建的通知记录摘要。"""
    channels = matching_channels(conn, event, project_id)
    if not channels:
        return []
    payload = build_payload(
        conn, event=event, title=title, project_id=project_id, data=data, link=link
    )
    repo = NotificationLogRepository(conn)
    created: list[dict[str, Any]] = []
    for channel in channels:
        log_id = repo.insert({
            "channel_id": channel["id"],
            "channel_name": channel["name"],
            "project_id": project_id,
            "event": event,
            "title": title[:200],
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "status": NOTIFICATION_PENDING,
            "attempts": 0,
            "created_at": utcnow_iso(),
        })
        task = job_queue.submit(
            conn,
            kind=KIND,
            title=f"通知 {channel['name']}：{EVENT_LABELS.get(event, event)}",
            payload={"logId": log_id},
            priority=120,
            max_attempts=3,
        )
        repo.update(log_id, {"task_id": int(task["id"])})
        created.append({"logId": log_id, "channelId": channel["id"], "taskId": task["id"]})
    return created


def send_webhook(url: str, payload: dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str]:
    """同步 POST 一次；HTTP 非 2xx 抛 ``HTTPError``，网络错误抛 ``URLError``。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "AITP-Notifier/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read(MAX_RESPONSE_CHARS).decode("utf-8", errors="replace")
        return int(response.status), text


def deliver(conn: sqlite3.Connection, log_id: int) -> dict[str, Any]:
    """执行一次投递；失败时落库后抛异常，交给队列按次数重试。"""
    repo = NotificationLogRepository(conn)
    log = repo.get(log_id)
    if log is None:
        raise RuntimeError("通知记录不存在")
    attempts = int(log.get("attempts") or 0) + 1
    channel = NotificationChannelRepository(conn).get(int(log["channel_id"])) \
        if log.get("channel_id") else None
    if channel is None or not channel.get("enabled"):
        repo.update(log_id, {
            "status": NOTIFICATION_FAILED,
            "attempts": attempts,
            "error_message": "通知渠道不存在或已停用",
            "finished_at": utcnow_iso(),
        })
        return {"status": NOTIFICATION_FAILED, "attempts": attempts}

    payload = json.loads(str(log.get("payload_json") or "{}"))
    try:
        http_status, response_text = send_webhook(str(channel.get("webhook_url") or ""), payload)
    except urllib.error.HTTPError as exc:
        message = f"HTTP {exc.code}"
        repo.update(log_id, {
            "status": NOTIFICATION_FAILED,
            "attempts": attempts,
            "http_status": int(exc.code),
            "response_text": (exc.read(MAX_RESPONSE_CHARS).decode("utf-8", errors="replace")),
            "error_message": message,
        })
        conn.commit()
        raise RuntimeError(f"Webhook 返回 {message}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        repo.update(log_id, {
            "status": NOTIFICATION_FAILED,
            "attempts": attempts,
            "error_message": str(exc)[:500],
        })
        conn.commit()
        raise RuntimeError(f"Webhook 调用失败：{exc}") from exc

    repo.update(log_id, {
        "status": NOTIFICATION_SUCCESS,
        "attempts": attempts,
        "http_status": http_status,
        "response_text": response_text,
        "error_message": "",
        "finished_at": utcnow_iso(),
    })
    return {"status": NOTIFICATION_SUCCESS, "attempts": attempts, "httpStatus": http_status}


def send_test(conn: sqlite3.Connection, channel_id: int) -> dict[str, Any]:
    """设置页「发一条测试通知」：同步发送并把结果写进发送记录。"""
    channel = NotificationChannelRepository(conn).get(channel_id)
    if channel is None:
        raise RuntimeError("通知渠道不存在")
    payload = build_payload(
        conn,
        event=EVENT_TEST,
        title="AITP 测试通知",
        project_id=channel.get("project_id"),
        data={"message": "如果你收到这条消息，说明 Webhook 地址可用。"},
        link="/settings/ci",
    )
    log_id = NotificationLogRepository(conn).insert({
        "channel_id": channel_id,
        "channel_name": channel.get("name"),
        "project_id": channel.get("project_id"),
        "event": EVENT_TEST,
        "title": payload["title"],
        "payload_json": json.dumps(payload, ensure_ascii=False),
        "status": NOTIFICATION_PENDING,
        "attempts": 1,
        "created_at": utcnow_iso(),
    })
    try:
        http_status, response_text = send_webhook(str(channel.get("webhook_url") or ""), payload)
    except urllib.error.HTTPError as exc:
        NotificationLogRepository(conn).update(log_id, {
            "status": NOTIFICATION_FAILED,
            "http_status": int(exc.code),
            "response_text": exc.read(MAX_RESPONSE_CHARS).decode("utf-8", errors="replace"),
            "error_message": f"HTTP {exc.code}",
            "finished_at": utcnow_iso(),
        })
        return {"success": False, "httpStatus": int(exc.code), "message": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        NotificationLogRepository(conn).update(log_id, {
            "status": NOTIFICATION_FAILED,
            "error_message": str(exc)[:500],
            "finished_at": utcnow_iso(),
        })
        return {"success": False, "httpStatus": 0, "message": str(exc)[:300]}
    NotificationLogRepository(conn).update(log_id, {
        "status": NOTIFICATION_SUCCESS,
        "http_status": http_status,
        "response_text": response_text,
        "finished_at": utcnow_iso(),
    })
    return {"success": True, "httpStatus": http_status, "message": response_text[:300]}


def notify_run_completed(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    row = TestRunRepository(conn).get_detail(run_id)
    if not row:
        return []
    run = run_summary(row)
    total = int(run.get("total") or 0)
    passed = int(run.get("passed") or 0)
    executed = total - int(run.get("notExecuted") or 0)
    pass_rate = round(passed / executed * 100, 2) if executed else 0.0
    data = {
        "runId": run.get("id"),
        "runName": run.get("name"),
        "versionId": run.get("versionId"),
        "total": total,
        "passed": passed,
        "failed": int(run.get("failed") or 0),
        "blocked": int(run.get("blocked") or 0),
        "notExecuted": int(run.get("notExecuted") or 0),
        "passRate": pass_rate,
        "finishedAt": run.get("finishedAt"),
    }
    return notify(
        conn,
        event=EVENT_RUN_COMPLETED,
        title=f"测试轮次完成：{run.get('name') or run_id}",
        project_id=run.get("projectId"),
        data=data,
        link=f"/test-runs/{run_id}",
    )


def notify_gate_blocked(conn: sqlite3.Connection, gate: dict[str, Any]) -> list[dict[str, Any]]:
    if str(gate.get("status") or "") != "blocked":
        return []
    raw_result = gate.get("result")
    result = raw_result if isinstance(raw_result, dict) else {}
    data = {
        "gateId": gate.get("id"),
        "gateName": gate.get("name"),
        "runId": gate.get("runId"),
        "versionId": gate.get("versionId"),
        "conclusion": gate.get("conclusion"),
        "failedChecks": result.get("failed") or result.get("failedChecks") or [],
    }
    return notify(
        conn,
        event=EVENT_GATE_BLOCKED,
        title=f"发布门禁未通过：{gate.get('name') or gate.get('id')}",
        project_id=gate.get("projectId"),
        data=data,
        link=f"/traceability?runId={gate.get('runId')}",
    )


def notify_automation_failed(
    conn: sqlite3.Connection, script: dict[str, Any], result: dict[str, Any]
) -> list[dict[str, Any]]:
    if str(result.get("status") or "") == "success":
        return []
    message = str(result.get("error_message") or result.get("stderr") or "")[-500:]
    category = str(script.get("category") or "web")
    data = {
        "scriptId": script.get("id"),
        "scriptNo": script.get("script_no"),
        "scriptName": script.get("name"),
        "automationRunId": result.get("id"),
        "runNo": result.get("run_no"),
        "status": result.get("status"),
        "exitCode": result.get("exit_code"),
        "message": message,
    }
    return notify(
        conn,
        event=EVENT_AUTOMATION_FAILED,
        title=f"自动化执行失败：{script.get('name') or script.get('script_no')}",
        project_id=script.get("project_id"),
        data=data,
        link=f"/automation/{category}",
    )


def notify_backup_failed(conn: sqlite3.Connection, message: str) -> list[dict[str, Any]]:
    return notify(
        conn,
        event=EVENT_BACKUP_FAILED,
        title="数据库备份失败",
        project_id=None,
        data={"message": message[:500]},
        link="/settings/backup",
    )


def notify_backup_done(
    conn: sqlite3.Connection, info: dict[str, Any], removed: list[str]
) -> list[dict[str, Any]]:
    return notify(
        conn,
        event=EVENT_BACKUP_DONE,
        title=f"自动备份完成：{info.get('name') or ''}",
        project_id=None,
        data={
            "name": info.get("name"),
            "sizeBytes": info.get("sizeBytes"),
            "files": info.get("files"),
            "retentionRemoved": removed,
        },
        link="/settings/backup",
    )


def notify_backup_skipped(conn: sqlite3.Connection, message: str) -> list[dict[str, Any]]:
    return notify(
        conn,
        event=EVENT_BACKUP_SKIPPED,
        title="自动备份已跳过",
        project_id=None,
        data={"message": message[:500]},
        link="/settings/backup",
    )


# ---------------- 队列处理器 ----------------

def run_notification_job(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    payload: dict[str, Any],
    db_path: Any,
) -> dict[str, Any]:
    log_id = int(payload.get("logId") or 0)
    if not log_id:
        raise RuntimeError("通知任务缺少 logId")
    return deliver(conn, log_id)


job_queue.register(KIND, run_notification_job)
