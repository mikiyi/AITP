"""任务清单服务。

清单是给人推进度用的，所以写操作都保持三条原则：
1. 改状态就顺手维护 completed_at 与父任务进度，不让人手工对齐；
2. 字段取值走白名单，非法值直接报参数错误，不静默降级；
3. 删父任务连带软删子任务，避免清单里留下没人认领的半截条目。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any

from ..common.time_utils import utcnow_iso
from ..common.validators import check_choice, optional_str, require_str
from ..errors import AppError, ErrorCode
from ..repositories.numbering import next_no
from ..repositories.todo_repo import (
    TODO_ACTIVE_STATUSES,
    TODO_CANCELLED,
    TODO_DOING,
    TODO_DONE,
    TODO_OPEN,
    TODO_PRIORITIES,
    TODO_STATUSES,
    TodoRepository,
    decorate,
)

#: 允许从请求体直接写入的列，其它键一律忽略
_EDITABLE_COLUMNS = {
    "title": "title",
    "description": "description",
    "status": "status",
    "priority": "priority",
    "category": "category",
    "tags": "tags",
    "dueDate": "due_date",
    "progress": "progress",
    "isPinned": "is_pinned",
    "projectId": "project_id",
    "moduleId": "module_id",
    "parentId": "parent_id",
}

DATE_FIELD = "dueDate"


def _clean_date(value: Any) -> str:
    """到期日只接受 YYYY-MM-DD；空值表示不设到期。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 dueDate 需为 YYYY-MM-DD 日期") from None
    return parsed.isoformat()


def _clean_int(value: Any, field: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 必须是整数") from None
    if number < low or number > high:
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 需在 {low}~{high} 之间")
    return number


def _normalize(conn: sqlite3.Connection, data: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    """把请求体整形成可落库的列，取值全部走白名单校验。"""
    payload: dict[str, Any] = {}
    for key, column in _EDITABLE_COLUMNS.items():
        if key not in data:
            continue
        raw = data[key]
        if column == "title":
            payload[column] = require_str(raw, "title", 200)
        elif column in ("description", "category", "tags"):
            payload[column] = optional_str(raw, key, 4000 if column == "description" else 200)
        elif column == "status":
            payload[column] = check_choice(str(raw or ""), TODO_STATUSES, "status", TODO_OPEN)
        elif column == "priority":
            payload[column] = check_choice(str(raw or ""), TODO_PRIORITIES, "priority", "medium")
        elif column == "due_date":
            payload[column] = _clean_date(raw)
        elif column == "progress":
            payload[column] = _clean_int(raw, "progress", 0, 100)
        elif column == "is_pinned":
            payload[column] = 1 if raw in (True, 1, "1", "true", "yes") else 0
        elif column in ("project_id", "module_id"):
            payload[column] = None if raw in (None, "", 0, "0") else _clean_int(raw, key, 1, 1 << 30)
        elif column == "parent_id":
            payload[column] = 0 if raw in (None, "", 0, "0") else _clean_int(raw, key, 1, 1 << 30)

    if creating:
        payload.setdefault("status", TODO_OPEN)
        payload.setdefault("priority", "medium")
        payload.setdefault("progress", 0)
        payload.setdefault("is_pinned", 0)
        payload.setdefault("parent_id", 0)
    return payload


def _guard_parent(conn: sqlite3.Connection, parent_id: Any, self_id: int | None = None) -> int:
    """父任务必须存在，且不能是自身或自己的子孙，否则清单会成环。"""
    pid = int(parent_id or 0)
    if pid == 0:
        return 0
    repo = TodoRepository(conn)
    parent = repo.get(pid)
    if parent is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "父任务不存在")
    if self_id is not None:
        # 从新父任务一路往上走，走到自己说明新父任务本来就在自己子树下
        cursor = pid
        seen: set[int] = set()
        while cursor:
            if cursor == self_id:
                raise AppError(ErrorCode.ILLEGAL_OPERATION, "不能把任务挂到自己的子树下")
            if cursor in seen:
                break
            seen.add(cursor)
            row = repo.get(cursor)
            cursor = int(row.get("parent_id") or 0) if row else 0
    return pid


def _rollup_parent(conn: sqlite3.Connection, parent_id: int) -> None:
    """子任务进度回推父任务：进度按完成条数算，全部收口则父任务转完成。"""
    if not parent_id:
        return
    repo = TodoRepository(conn)
    parent = repo.get(int(parent_id))
    if parent is None:
        return
    closed, total = repo.child_progress(int(parent_id))
    if total == 0:
        return
    progress = int(round(closed * 100 / total))
    fields: dict[str, Any] = {"progress": progress}
    if closed == total and str(parent.get("status")) != TODO_DONE:
        fields.update({"status": TODO_DONE, "completed_at": utcnow_iso()})
    elif str(parent.get("status")) == TODO_DONE and closed < total:
        # 父任务被手工标完成后，子任务又退回去了，父任务跟着回到进行中
        fields.update({"status": TODO_DOING, "completed_at": None})
    repo.update(int(parent_id), fields)


def create_todo(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    title = require_str(data.get("title"), "title", 200)
    payload = _normalize(conn, data, creating=True)
    payload["title"] = title
    payload["parent_id"] = _guard_parent(conn, payload.get("parent_id", 0))
    payload["todo_no"] = next_no(conn, "biz_todo", "todo_no", "TD")
    payload["sort_order"] = TodoRepository(conn).next_sort_order(int(payload["parent_id"]))
    if payload["status"] in (TODO_DONE, TODO_CANCELLED):
        payload["completed_at"] = utcnow_iso()
    new_id = TodoRepository(conn).insert(payload)
    _rollup_parent(conn, int(payload["parent_id"]))
    row = TodoRepository(conn).get(new_id)
    return decorate(conn, row or {})


def update_todo(conn: sqlite3.Connection, pk: int, data: dict[str, Any]) -> dict[str, Any]:
    repo = TodoRepository(conn)
    row = repo.get(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    payload = _normalize(conn, data, creating=False)
    if "parent_id" in payload:
        payload["parent_id"] = _guard_parent(conn, payload["parent_id"], self_id=pk)
        payload["sort_order"] = repo.next_sort_order(int(payload["parent_id"]))
    # 状态与完成时间成对维护，避免出现「已完成但没有完成时间」
    status = payload.get("status")
    if status is not None:
        if status in (TODO_DONE, TODO_CANCELLED):
            payload["completed_at"] = str(row.get("completed_at") or "") or utcnow_iso()
            payload.setdefault("progress", 100 if status == TODO_DONE else int(row.get("progress") or 0))
        elif str(row.get("status")) in (TODO_DONE, TODO_CANCELLED):
            payload["completed_at"] = None
            payload.setdefault("progress", 0)
    elif payload.get("progress") == 100 and str(row.get("status")) not in (TODO_DONE, TODO_CANCELLED):
        payload["status"] = TODO_DONE
        payload["completed_at"] = utcnow_iso()
    repo.update(pk, payload)
    if payload.get("status") is not None or payload.get("progress") is not None:
        _rollup_parent(conn, int(row.get("parent_id") or 0))
    updated = repo.get(pk)
    return decorate(conn, updated or {})


def toggle_done(conn: sqlite3.Connection, pk: int) -> dict[str, Any]:
    """勾选框：未完成→完成，已完成→回到待办。"""
    repo = TodoRepository(conn)
    row = repo.get(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    done = str(row.get("status")) == TODO_DONE
    if done:
        repo.update(pk, {"status": TODO_OPEN, "completed_at": None, "progress": 0})
    else:
        repo.update(pk, {"status": TODO_DONE, "completed_at": utcnow_iso(), "progress": 100})
    _rollup_parent(conn, int(row.get("parent_id") or 0))
    updated = repo.get(pk)
    return decorate(conn, updated or {})


def set_status(conn: sqlite3.Connection, pk: int, status: str) -> dict[str, Any]:
    return update_todo(conn, pk, {"status": check_choice(status, TODO_STATUSES, "status")})


def remove_todo(conn: sqlite3.Connection, pk: int) -> dict[str, Any]:
    """软删任务，子任务一并软删；返回被删掉的 id 列表。"""
    repo = TodoRepository(conn)
    row = repo.get(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    removed = [pk]
    for child in repo.list_children(pk):
        child_id = int(child["id"])
        if repo.soft_delete(child_id):
            removed.append(child_id)
    repo.soft_delete(pk)
    _rollup_parent(conn, int(row.get("parent_id") or 0))
    return {"id": pk, "removed": removed, "count": len(removed)}


def batch_update(conn: sqlite3.Connection, ids: list[int], data: dict[str, Any]) -> dict[str, Any]:
    """批量改状态 / 优先级 / 分类，返回真正被改到的 id。"""
    repo = TodoRepository(conn)
    payload = _normalize(conn, data, creating=False)
    if not payload:
        raise AppError(ErrorCode.PARAM_ERROR, "没有可更新的字段")
    updated: list[int] = []
    touched_parents: set[int] = set()
    for item in ids:
        row = repo.get(item)
        if row is None:
            continue
        fields = dict(payload)
        status = fields.get("status")
        if status == TODO_DONE:
            fields["completed_at"] = utcnow_iso()
            fields["progress"] = 100
        elif status in (TODO_CANCELLED,):
            fields["completed_at"] = utcnow_iso()
        elif status in TODO_ACTIVE_STATUSES:
            fields["completed_at"] = None
        repo.update(item, fields)
        updated.append(item)
        parent_id = int(row.get("parent_id") or 0)
        if parent_id:
            touched_parents.add(parent_id)
    for parent_id in touched_parents:
        _rollup_parent(conn, parent_id)
    return {"updated": updated, "count": len(updated), "skipped": [i for i in ids if i not in updated]}


def reorder(conn: sqlite3.Connection, ids: list[int]) -> dict[str, Any]:
    """按给定顺序重写 sort_order，用于拖拽或上移下移。"""
    repo = TodoRepository(conn)
    ordered: list[int] = []
    for index, item in enumerate(ids, start=1):
        row = repo.get(item)
        if row is None:
            continue
        repo.update(item, {"sort_order": index})
        ordered.append(item)
    return {"ordered": ordered, "count": len(ordered)}


def move(conn: sqlite3.Connection, pk: int, direction: str) -> dict[str, Any]:
    """在同一层内上移/下移一位，交换相邻 sort_order。"""
    if direction not in ("up", "down"):
        raise AppError(ErrorCode.PARAM_ERROR, "参数 direction 只能是 up / down")
    repo = TodoRepository(conn)
    row = repo.get(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    parent_id = int(row.get("parent_id") or 0)
    siblings = repo.list_children(parent_id) if parent_id else _root_siblings(conn)
    current = next((i for i, item in enumerate(siblings) if int(item["id"]) == pk), None)
    if current is None:
        return {"moved": False, "reason": "同级任务未找到"}
    target = current - 1 if direction == "up" else current + 1
    if target < 0 or target >= len(siblings):
        return {"moved": False, "reason": "已经在边界"}
    a, b = siblings[current], siblings[target]
    repo.update(int(a["id"]), {"sort_order": int(b.get("sort_order") or 0)})
    repo.update(int(b["id"]), {"sort_order": int(a.get("sort_order") or 0)})
    return {"moved": True, "ids": [int(a["id"]), int(b["id"])]}


def _root_siblings(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM biz_todo WHERE parent_id = 0 AND is_deleted = 0"
        " ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def list_todos(
    conn: sqlite3.Connection,
    *,
    view: str = "all",
    filters: dict[str, Any] | None = None,
    keyword: str | None = None,
    page_num: int = 1,
    page_size: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    """取一页顶层任务，并为每条带上展开用的子任务。"""
    repo = TodoRepository(conn)
    page = repo.list_view(view, filters, keyword, page_num=page_num, page_size=page_size)
    items = [decorate(conn, dict(row)) for row in page.list]
    for item in items:
        children = repo.list_children(int(item["id"]))
        item["children"] = [decorate(conn, child) for child in children]
    return items, int(page.total)


def export_rows(conn: sqlite3.Connection, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """导出用扁平行：只导顶层任务，子任务在标题里标注。"""
    repo = TodoRepository(conn)
    rows = repo.list_all(filters or {}, None, limit=1000)
    today = date.today().isoformat()
    status_labels = {"open": "待办", "doing": "进行中", "done": "已完成", "cancelled": "已取消"}
    priority_labels = {"urgent": "紧急", "high": "高", "medium": "中", "low": "低"}
    output: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "")
        due = str(row.get("due_date") or "")
        output.append({
            "todoNo": row.get("todo_no"),
            "title": row.get("title"),
            "status": status_labels.get(status, status),
            "priority": priority_labels.get(str(row.get("priority")), str(row.get("priority"))),
            "category": row.get("category") or "",
            "dueDate": due,
            "isOverdue": "是" if due and due < today and status in TODO_ACTIVE_STATUSES else "否",
            "progress": int(row.get("progress") or 0),
            "tags": row.get("tags") or "",
            "description": row.get("description") or "",
            "completedAt": row.get("completed_at") or "",
            "createdAt": row.get("created_at"),
        })
    return output
