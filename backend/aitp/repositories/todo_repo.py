"""任务清单仓储。

一张表装下「要做的事」：主任务与子任务同表（parent_id 自引用），
状态 / 优先级 / 到期日 / 分类都是可筛选列。
到期日按 YYYY-MM-DD 存字符串，字典序即时间序，直接比较不用换算时区。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from ..common.pagination import Page, RowDict, RowList, build_page
from .base import BaseRepository

#: 任务状态
TODO_OPEN = "open"
TODO_DOING = "doing"
TODO_DONE = "done"
TODO_CANCELLED = "cancelled"

TODO_STATUSES = (TODO_OPEN, TODO_DOING, TODO_DONE, TODO_CANCELLED)
#: 还没做完的状态，逾期统计只看这几个
TODO_ACTIVE_STATUSES = (TODO_OPEN, TODO_DOING)

#: 优先级，数值越小越靠前
TODO_LOW = "low"
TODO_MEDIUM = "medium"
TODO_HIGH = "high"
TODO_URGENT = "urgent"

TODO_PRIORITIES = (TODO_URGENT, TODO_HIGH, TODO_MEDIUM, TODO_LOW)
PRIORITY_RANK = {TODO_URGENT: 0, TODO_HIGH: 1, TODO_MEDIUM: 2, TODO_LOW: 3}

#: 快捷视图
VIEW_ALL = "all"
VIEW_TODAY = "today"
VIEW_WEEK = "week"
VIEW_OVERDUE = "overdue"
VIEW_DONE = "done"
VIEW_STARRED = "starred"

TODO_VIEWS = (VIEW_ALL, VIEW_TODAY, VIEW_WEEK, VIEW_OVERDUE, VIEW_DONE, VIEW_STARRED)


class TodoRepository(BaseRepository):
    table = "biz_todo"
    default_order = "sort_order"
    default_order_dir = "asc"
    order_whitelist = {
        "id", "todo_no", "title", "status", "priority", "due_date",
        "category", "progress", "sort_order", "created_at", "updated_at", "completed_at",
    }
    searchable = ["title", "description", "category", "tags", "todo_no"]

    # ---- 查询 ----
    def list_view(
        self,
        view: str = VIEW_ALL,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        page_num: int = 1,
        page_size: int = 100,
        order_by: str | None = None,
    ) -> Page:
        """按快捷视图取顶层任务；视图条件写进 where，不靠取全量再筛。"""
        merged = dict(filters or {})
        extra = self._view_clause(view)
        where, params = self._build_where(merged, keyword)
        if extra:
            clause, clause_params = extra
            where = f"{where} AND {clause}"
            params.extend(clause_params)
        total = int(
            self.conn.execute(
                f"SELECT COUNT(*) FROM biz_todo WHERE {where}", params
            ).fetchone()[0]
        )
        limit = max(1, min(int(page_size or 100), 500))
        offset = (max(1, int(page_num or 1)) - 1) * limit
        order = self._safe_order(order_by)
        direction = "DESC" if order == "created_at" else "ASC"
        # 置顶优先，其次按优先级（紧急→低），最后落到调用方指定的排序列
        rows = self.conn.execute(
            f"SELECT * FROM biz_todo WHERE {where} "
            "ORDER BY is_pinned DESC, "
            "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END ASC, "
            f"sort_order {direction}, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return build_page(rows, total, page_num, limit)

    def _view_clause(self, view: str) -> tuple[str, list[Any]] | None:
        today = date.today().isoformat()
        week_end = (date.today() + timedelta(days=7)).isoformat()
        if view == VIEW_TODAY:
            return "parent_id = 0 AND status IN (?, ?) AND due_date <= ?", [*TODO_ACTIVE_STATUSES, today]
        if view == VIEW_WEEK:
            return (
                "parent_id = 0 AND status IN (?, ?) AND due_date IS NOT NULL "
                "AND due_date != '' AND due_date <= ?",
                [*TODO_ACTIVE_STATUSES, week_end],
            )
        if view == VIEW_OVERDUE:
            return (
                "parent_id = 0 AND status IN (?, ?) AND due_date IS NOT NULL "
                "AND due_date != '' AND due_date < ?",
                [*TODO_ACTIVE_STATUSES, today],
            )
        if view == VIEW_DONE:
            return "parent_id = 0 AND status IN (?, ?)", [TODO_DONE, TODO_CANCELLED]
        if view == VIEW_STARRED:
            return "parent_id = 0 AND is_pinned = 1", []
        return "parent_id = 0", []

    def list_children(self, parent_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_todo WHERE parent_id = ? AND is_deleted = 0"
            " ORDER BY sort_order ASC, id ASC",
            (parent_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_with_children(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if row is None:
            return None
        row["children"] = self.list_children(int(pk))
        return row

    def next_sort_order(self, parent_id: int) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM biz_todo"
            " WHERE parent_id = ? AND is_deleted = 0",
            (parent_id,),
        ).fetchone()
        return int(row[0]) + 1

    # ---- 统计 ----
    def stats(self, project_id: int | None = None) -> dict[str, Any]:
        """总量 / 未完成 / 今天到期 / 逾期 / 已完成，一次算完给前端。"""
        today = date.today().isoformat()
        clauses = ["is_deleted = 0"]
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(int(project_id))
        where = " AND ".join(clauses)
        row = self.conn.execute(
            f"SELECT"
            f" COUNT(*) AS total,"
            f" SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open,"
            f" SUM(CASE WHEN status = 'doing' THEN 1 ELSE 0 END) AS doing,"
            f" SUM(CASE WHEN status IN ('open','doing') THEN 1 ELSE 0 END) AS active,"
            f" SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done,"
            f" SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,"
            f" SUM(CASE WHEN status IN ('open','doing') AND due_date = ? THEN 1 ELSE 0 END) AS due_today,"
            f" SUM(CASE WHEN status IN ('open','doing') AND due_date IS NOT NULL"
            f" AND due_date != '' AND due_date < ? THEN 1 ELSE 0 END) AS overdue,"
            f" SUM(CASE WHEN is_pinned = 1 THEN 1 ELSE 0 END) AS pinned"
            f" FROM biz_todo WHERE {where}",
            [today, today, *params],
        ).fetchone()
        stats = {key: int(row[key] or 0) for key in row.keys()} if row else {}
        active = stats.get("active", 0)
        total = stats.get("total", 0)
        done = stats.get("done", 0)
        return {
            "total": total,
            "active": active,
            "open": stats.get("open", 0),
            "doing": stats.get("doing", 0),
            "done": done,
            "cancelled": stats.get("cancelled", 0),
            "dueToday": stats.get("due_today", 0),
            "overdue": stats.get("overdue", 0),
            "pinned": stats.get("pinned", 0),
            "completionRate": round(done / total, 4) if total else 0.0,
        }

    def status_stats(self, project_id: int | None = None) -> dict[str, int]:
        clauses = ["is_deleted = 0"]
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(int(project_id))
        rows = self.conn.execute(
            f"SELECT status, COUNT(*) AS c FROM biz_todo WHERE {' AND '.join(clauses)}"
            " GROUP BY status",
            params,
        ).fetchall()
        counts = {status: 0 for status in TODO_STATUSES}
        for row in rows:
            counts[str(row["status"])] = int(row["c"])
        return counts

    def categories(self, project_id: int | None = None) -> list[dict[str, Any]]:
        """分类清单带未完成条数，供左侧筛选栏直接显示。"""
        clauses = ["is_deleted = 0", "category IS NOT NULL", "category != ''"]
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(int(project_id))
        rows = self.conn.execute(
            f"SELECT category, COUNT(*) AS total,"
            f" SUM(CASE WHEN status IN ('open','doing') THEN 1 ELSE 0 END) AS active"
            f" FROM biz_todo WHERE {' AND '.join(clauses)}"
            " GROUP BY category ORDER BY category ASC",
            params,
        ).fetchall()
        return [
            {
                "category": str(row["category"]),
                "total": int(row["total"]),
                "active": int(row["active"] or 0),
            }
            for row in rows
        ]

    def is_done_tree(self, pk: int) -> bool:
        """子任务是否全部收口，用于父任务进度回推。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN status IN ('done','cancelled') THEN 1 ELSE 0 END) AS closed"
            " FROM biz_todo WHERE parent_id = ? AND is_deleted = 0",
            (pk,),
        ).fetchone()
        total = int(row["total"] or 0)
        return total > 0 and int(row["closed"] or 0) == total

    def child_progress(self, pk: int) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN status IN ('done','cancelled') THEN 1 ELSE 0 END) AS closed"
            " FROM biz_todo WHERE parent_id = ? AND is_deleted = 0",
            (pk,),
        ).fetchone()
        return int(row["closed"] or 0), int(row["total"] or 0)


def todo_summary(row: dict[str, Any]) -> dict[str, Any]:
    """接口出参统一驼峰，日期与布尔在这里定型。"""
    return {
        "id": row.get("id"),
        "todoNo": row.get("todo_no"),
        "parentId": row.get("parent_id") or 0,
        "projectId": row.get("project_id"),
        "moduleId": row.get("module_id"),
        "title": row.get("title"),
        "description": row.get("description") or "",
        "status": row.get("status"),
        "priority": row.get("priority"),
        "category": row.get("category") or "",
        "tags": row.get("tags") or "",
        "dueDate": row.get("due_date") or "",
        "progress": int(row.get("progress") or 0),
        "isPinned": bool(row.get("is_pinned")),
        "sortOrder": int(row.get("sort_order") or 0),
        "completedAt": row.get("completed_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def decorate(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    """补上列表页要直接显示的派生字段：逾期标记、子任务完成数。"""
    payload = todo_summary(row)
    today = date.today().isoformat()
    due = str(payload["dueDate"] or "")
    payload["isOverdue"] = bool(
        due and due < today and payload["status"] in TODO_ACTIVE_STATUSES
    )
    payload["isDueToday"] = bool(due == today and payload["status"] in TODO_ACTIVE_STATUSES)
    closed, total = TodoRepository(conn).child_progress(int(payload["id"] or 0))
    payload["childDone"] = closed
    payload["childTotal"] = total
    return payload
