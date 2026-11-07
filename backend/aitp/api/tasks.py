"""任务队列 API：查看排队/执行/失败任务，取消与重试。"""

from __future__ import annotations

from flask import Blueprint, Response, request

from ..common.validators import check_choice
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import audit_service, job_queue
from .helpers import audit, commit, db, page_args, path_int

bp = Blueprint("tasks", __name__)


@bp.get("")
def list_tasks() -> Response:
    conn = db()
    page_num, page_size = page_args()
    status = str(request.args.get("status") or "").strip()
    if status:
        status = check_choice(status, job_queue.TASK_STATUSES, "status") or ""
    kind = str(request.args.get("kind") or "").strip()
    keyword = str(request.args.get("keyword") or "").strip()
    payload = job_queue.list_tasks(
        conn,
        status=status,
        kind=kind,
        keyword=keyword,
        page_num=page_num,
        page_size=page_size,
    )
    payload["stats"] = job_queue.stats(conn)
    payload["kinds"] = [
        {"value": key, "label": label} for key, label in job_queue.KIND_LABELS.items()
    ]
    return ok(payload)


@bp.get("/<pk>")
def get_task(pk: str) -> Response:
    conn = db()
    task = job_queue.get_task(conn, path_int(pk))
    if task is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    return ok(task)


@bp.post("/<pk>/cancel")
def cancel_task(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    task = job_queue.cancel_task(conn, target)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TASK, target,
          str(task.get("title") or ""), {"action": "cancel"})
    return ok(task)


@bp.post("/<pk>/retry")
def retry_task(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    task = job_queue.retry_task(conn, target)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TASK, target,
          str(task.get("title") or ""), {"action": "retry"})
    return ok(task)


@bp.delete("/<pk>")
def delete_task(pk: str) -> Response:
    """只允许删除已结束的任务，运行中的任务先取消。"""
    conn = db()
    target = path_int(pk)
    task = job_queue.get_task(conn, target)
    if task is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    if str(task.get("status")) not in (
        job_queue.TASK_SUCCESS, job_queue.TASK_FAILED, job_queue.TASK_CANCELLED
    ):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "任务尚未结束，不能删除")
    conn.execute("DELETE FROM sys_task WHERE id = ?", (target,))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TASK, target,
          str(task.get("title") or ""))
    return ok({"id": target, "deleted": True})
