"""AI 会话、消息与任务仓储。"""

from __future__ import annotations

import json
from typing import Any

from ..common.pagination import MAX_PAGE_SIZE, Page, RowDict, RowList, build_page
from ..common.time_utils import utcnow_iso
from .base import BaseRepository, loads


class AISessionRepository(BaseRepository):
    table = "biz_ai_session"
    default_order = "updated_at"
    order_whitelist = {"id", "title", "module", "created_at", "updated_at"}
    searchable = ["title"]

    def page_scoped(
        self,
        filters: dict[str, Any] | None,
        project_id: int | None,
        page_num: int,
        page_size: int,
    ) -> Page:
        """会话表没有 project_id 列，项目维度只能顺着它名下的任务收敛。

        不给 project 时就是原来的全局列表；给了就只留该项目跑过的会话，
        否则项目页的「会话记录」会把别人的对话一起列出来。
        """
        where, params = self._build_where(filters, None)
        if project_id:
            where += (
                " AND EXISTS (SELECT 1 FROM biz_ai_task t WHERE t.session_id = biz_ai_session.id"
                " AND t.is_deleted = 0 AND t.project_id = ?)"
            )
            params = [*params, int(project_id)]
        total = int(self.conn.execute(
            f"SELECT COUNT(*) FROM {self.table} WHERE {where}", params
        ).fetchone()[0])
        limit = max(1, min(int(page_size or 20), MAX_PAGE_SIZE))
        offset = (max(1, int(page_num or 1)) - 1) * limit
        rows = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE {where} ORDER BY {self.default_order}"
            f" {self.default_order_dir.upper()} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return build_page([dict(row) for row in rows], total, page_num, limit)

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if not row:
            return None
        row["messages"] = self.list_messages(pk)
        return row

    def list_messages(self, session_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_ai_message WHERE session_id = ? AND is_deleted = 0"
            " ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        messages: RowList = []
        for row in rows:
            message = dict(row)
            message["meta"] = loads(message.get("message_meta"), {}) or {}
            message.pop("message_meta", None)
            messages.append(message)
        return messages

    def add_message(self, session_id: int, role: str, content: str, **fields: Any) -> int:
        return AIMessageRepository(self.conn).add(session_id, role, content, **fields)


class AIMessageRepository(BaseRepository):
    table = "biz_ai_message"
    default_order = "id"
    default_order_dir = "asc"
    order_whitelist = {"id", "role", "created_at"}
    searchable = ["content"]

    def add(self, session_id: int, role: str, content: str, **fields: Any) -> int:
        payload = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": fields.pop("created_at", None),
            "updated_at": fields.pop("updated_at", None),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        payload.update(fields)
        return self.insert(payload)


class AITaskRepository(BaseRepository):
    table = "biz_ai_task"
    default_order = "created_at"
    order_whitelist = {"id", "task_type", "status", "created_at", "updated_at"}
    searchable = ["task_type", "status"]

    def recent(self, limit: int = 5, project_id: int | None = None) -> RowList:
        """首页「最近任务」：给了项目就只看本项目，且必须按时间倒序取。"""
        scope = " AND project_id = ?" if project_id else ""
        params: list[Any] = [int(project_id)] if project_id else []
        params.append(int(limit))
        rows = self.conn.execute(
            f"SELECT * FROM biz_ai_task WHERE is_deleted = 0{scope} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def start(self, task_type: str, **fields: Any) -> int:
        payload = {
            "task_type": task_type,
            "status": "running",
            "started_at": utcnow_iso(),
            **fields,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return self.insert(payload)

    def finish(self, pk: int, status: str, result: dict[str, Any] | None = None,
               error_code: int | None = None, error_message: str | None = None) -> bool:
        payload: dict[str, Any] = {
            "status": status,
            "finished_at": utcnow_iso(),
        }
        if result is not None:
            payload["result_json"] = json.dumps(result, ensure_ascii=False)
        if error_code is not None:
            payload["error_code"] = error_code
        if error_message is not None:
            payload["error_message"] = error_message
        return self.update(pk, payload)

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if not row:
            return None
        row["request"] = loads(row.get("request_json"), {}) or {}
        row["result"] = loads(row.get("result_json"), {}) or {}
        row.pop("request_json", None)
        row.pop("result_json", None)
        return row
