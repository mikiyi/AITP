"""仓储基类：纯 SQL，无 ORM。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, cast

from ..common.pagination import MAX_PAGE_SIZE, Page, RowDict, RowList, build_page
from ..common.time_utils import utcnow_iso

# 参数列表：SQL 占位符对应的原始值
ParamList = list[Any]
# sqlite3.Row 列表
SqlRowList = list[sqlite3.Row]

#: 筛选值哨兵：表示「该字段未挂值」，用于「未关联功能点」这类空值筛选
NULL_FILTER = "__null__"


class BaseRepository:
    table: str = ""
    soft_delete_enabled: bool = True
    default_order: str = "created_at"
    default_order_dir: str = "desc"
    order_whitelist: set[str] = set()
    searchable: list[str] = []

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- 写 ----
    def insert(self, data: dict[str, Any]) -> int:
        payload = dict(data)
        now = utcnow_iso()
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        if self.soft_delete_enabled:
            payload.setdefault("is_deleted", 0)
        columns = ", ".join(payload.keys())
        placeholders = ", ".join(f":{k}" for k in payload)
        cursor = self.conn.execute(
            f"INSERT INTO {self.table} ({columns}) VALUES ({placeholders})", payload
        )
        return int(cast(int, cursor.lastrowid))

    def update(self, pk: int, data: dict[str, Any]) -> bool:
        if not data:
            return False
        payload = dict(data)
        payload["updated_at"] = utcnow_iso()
        assignments = ", ".join(f"{k} = :{k}" for k in payload)
        payload["__pk"] = pk
        cursor = self.conn.execute(
            f"UPDATE {self.table} SET {assignments} WHERE id = :__pk", payload
        )
        return cursor.rowcount > 0

    def soft_delete(self, pk: int) -> bool:
        if not self.soft_delete_enabled:
            return self.delete(pk)
        cursor = self.conn.execute(
            f"UPDATE {self.table} SET is_deleted = 1, updated_at = ? WHERE id = ? AND is_deleted = 0",
            (utcnow_iso(), pk),
        )
        return cursor.rowcount > 0

    def delete(self, pk: int) -> bool:
        cursor = self.conn.execute(f"DELETE FROM {self.table} WHERE id = ?", (pk,))
        return cursor.rowcount > 0

    # ---- 读 ----
    def get(self, pk: int) -> RowDict | None:
        where = "id = ?" + (" AND is_deleted = 0" if self.soft_delete_enabled else "")
        row = self.conn.execute(f"SELECT * FROM {self.table} WHERE {where}", (pk,)).fetchone()
        return dict(row) if row else None

    def existing_ids(self, ids: Iterable[int]) -> set[int]:
        """给定主键里哪些真实存在（软删除的不算），用于建关联前的存在性校验。"""
        values = [int(i) for i in ids]
        if not values:
            return set()
        placeholders = ", ".join("?" for _ in values)
        where = f"id IN ({placeholders})"
        if self.soft_delete_enabled:
            where += " AND is_deleted = 0"
        rows = self.conn.execute(f"SELECT id FROM {self.table} WHERE {where}", values).fetchall()
        return {int(r["id"]) for r in rows}

    def count(self, filters: dict[str, Any] | None = None, keyword: str | None = None) -> int:
        where, params = self._build_where(filters, keyword)
        row = self.conn.execute(f"SELECT COUNT(*) FROM {self.table} WHERE {where}", params).fetchone()
        return int(row[0])

    def list(
        self,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        page_num: int = 1,
        page_size: int = 20,
        order_by: str | None = None,
        order: str | None = None,
    ) -> Page:
        where, params = self._build_where(filters, keyword)
        total = self.count(filters, keyword)
        limit = max(1, min(int(page_size or 20), MAX_PAGE_SIZE))
        offset = (max(1, int(page_num or 1)) - 1) * limit
        direction = (order or self.default_order_dir).lower()
        if direction not in ("asc", "desc"):
            direction = self.default_order_dir
        sql = (
            f"SELECT * FROM {self.table} WHERE {where} "
            f"ORDER BY {self._safe_order(order_by)} {direction.upper()} LIMIT ? OFFSET ?"
        )
        rows = self.conn.execute(sql, [*params, limit, offset]).fetchall()
        return build_page(rows, total, page_num, limit)

    def list_all(
        self, filters: dict[str, Any] | None = None, keyword: str | None = None, limit: int = 1000
    ) -> RowList:
        where, params = self._build_where(filters, keyword)
        rows = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE {where} LIMIT ?", [*params, int(limit)]
        ).fetchall()
        return [dict(r) for r in rows]

    def raw(self, sql: str, params: Iterable[Any] = ()) -> SqlRowList:
        return self.conn.execute(sql, tuple(params)).fetchall()

    def raw_one(self, sql: str, params: Iterable[Any] = ()) -> RowDict | None:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    # ---- 内部 ----
    def _build_where(
        self, filters: dict[str, Any] | None, keyword: str | None
    ) -> tuple[str, ParamList]:
        clauses: list[str] = []
        params: ParamList = []

        if self.soft_delete_enabled:
            clauses.append("is_deleted = 0")

        for key, value in (filters or {}).items():
            if value in (None, ""):
                continue
            if isinstance(value, (list, tuple, set)):
                values = [v for v in value if v not in (None, "")]
                if not values:
                    continue
                placeholders = ", ".join("?" for _ in values)
                clauses.append(f"{key} IN ({placeholders})")
                params.extend(values)
            elif isinstance(value, bool):
                clauses.append(f"{key} = ?")
                params.append(1 if value else 0)
            elif value == NULL_FILTER:
                clauses.append(f"{key} IS NULL")
            else:
                clauses.append(f"{key} = ?")
                params.append(value)

        if keyword and self.searchable:
            parts = " OR ".join(f"instr(COALESCE({col}, ''), ?) > 0" for col in self.searchable)
            clauses.append(f"({parts})")
            params.extend([keyword] * len(self.searchable))

        where = " AND ".join(clauses) if clauses else "1=1"
        return where, params

    def _safe_order(self, order_by: str | None) -> str:
        if not order_by:
            return self.default_order
        parts = [p.strip() for p in order_by.strip().split(",") if p.strip()]
        if not parts:
            return self.default_order
        if self.order_whitelist:
            if any(p not in self.order_whitelist for p in parts):
                return self.default_order
        elif not all(_is_safe_identifier(p) for p in parts):
            return self.default_order
        return ", ".join(parts)


def _is_safe_identifier(value: str) -> bool:
    return value.replace("_", "").isalnum() and not value[0].isdigit()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
