"""仓储基类：纯 SQL，无 ORM。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from typing import Any, Iterable, cast

from ..common.pagination import MAX_PAGE_SIZE, Page, RowDict, RowList, build_page
from ..common.time_utils import utcnow_iso

# 参数列表：SQL 占位符对应的原始值
ParamList = list[Any]
# sqlite3.Row 列表
SqlRowList = list[sqlite3.Row]
# 下面两个别名供类内方法签名用：BaseRepository 有个叫 list 的方法，
# 类体里直接写 list[int] / list[str] 会解析到那个方法而不是内置 list，
# 所以在模块作用域（内置 list 可见）先取好别名再引用。
IdBatchList = list[int]
StrList = list[str]

#: 筛选值哨兵：表示「该字段未挂值」，用于「未关联功能点」这类空值筛选
NULL_FILTER = "__null__"

#: 扫描式读取单批行数上限：导出与游标接口每批最多读这么多行，绝不整表载入内存
SCAN_PAGE_SIZE = 500
#: 单次扫描累计行数上限：超过就停止，给导出处一个可验证的硬上界
SCAN_MAX_ROWS = 50_000


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
            f"ORDER BY {self._safe_order(order_by)} {direction.upper()}, id {direction.upper()}"
            f" LIMIT ? OFFSET ?"
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

    # ---- 扫描式读取：导出 / 游标接口的替代 list_all 写法 ----
    def iter_rows(
        self,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        *,
        batch: int = SCAN_PAGE_SIZE,
        max_rows: int = SCAN_MAX_ROWS,
        order_by: str | None = None,
        order: str | None = None,
        columns: str = "*",
    ) -> Iterator[RowList]:
        """按批 yield 行，单批不超过 SCAN_PAGE_SIZE 行、总量不超过 max_rows 行。

        导出与需要遍历全量的接口用它，不要用 list_all(limit=10000) 一次载入全表：
        内存只留一批，SQLite 也只读一批。排序与 list() 一致（附带 id 决胜），
        所以同一批数据在列表页与导出文件里顺序相同。
        """
        where, params = self._build_where(filters, keyword)
        size = max(1, min(int(batch or SCAN_PAGE_SIZE), SCAN_PAGE_SIZE))
        ceiling = max(0, int(max_rows or SCAN_MAX_ROWS))
        direction = (order or self.default_order_dir).lower()
        if direction not in ("asc", "desc"):
            direction = self.default_order_dir
        order_sql = f"ORDER BY {self._safe_order(order_by)} {direction.upper()}, id {direction.upper()}"
        offset = 0
        scanned = 0
        while ceiling == 0 or scanned < ceiling:
            want = size if ceiling == 0 else min(size, ceiling - scanned)
            rows = self.conn.execute(
                f"SELECT {columns} FROM {self.table} WHERE {where} "
                f"{order_sql} LIMIT ? OFFSET ?",
                [*params, want, offset],
            ).fetchall()
            if not rows:
                return
            chunk = [dict(r) for r in rows]
            scanned += len(chunk)
            offset += len(chunk)
            yield chunk
            if len(chunk) < want:
                return

    def iter_ids(
        self,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        *,
        batch: int = SCAN_PAGE_SIZE,
        max_rows: int = SCAN_MAX_ROWS,
    ) -> Iterator[IdBatchList]:
        """只要主键的批量扫描：读放大远低于 iter_rows，供「按范围选一批 id」用。"""
        for chunk in self.iter_rows(
            filters, keyword, batch=batch, max_rows=max_rows, columns="id"
        ):
            yield [int(row["id"]) for row in chunk]

    def scan_column(
        self,
        column: str,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        *,
        max_rows: int = SCAN_MAX_ROWS,
    ) -> StrList:
        """单列值清单（如已有用例编号），只读一列，不整行载入。"""
        if not _is_safe_identifier(column):
            raise ValueError(f"非法列名：{column}")
        values: list[str] = []
        for chunk in self.iter_rows(
            filters, keyword, columns=column, max_rows=max_rows, order_by=column, order="asc"
        ):
            values.extend(str(row[column]) for row in chunk if row.get(column) is not None)
        return values

    def page_after_id(
        self,
        filters: dict[str, Any] | None = None,
        *,
        after_id: int = 0,
        batch: int = SCAN_PAGE_SIZE,
    ) -> RowList:
        """主键游标的下一页：取 id 大于游标的一批，按 id 升序；返回空列表即游标耗尽。"""
        where, params = self._build_where(filters, None)
        size = max(1, min(int(batch or SCAN_PAGE_SIZE), SCAN_PAGE_SIZE))
        rows = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE {where} AND id > ? ORDER BY id ASC LIMIT ?",
            [*params, int(after_id or 0), size],
        ).fetchall()
        return [dict(r) for r in rows]

    def group_counts(
        self,
        columns: tuple[str, ...],
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
    ) -> RowList:
        """按少数几列做 COUNT 汇总：万级数据只要几十行结果，取代把整表读进内存数数。"""
        select = ", ".join(columns)
        for column in columns:
            if not _is_safe_identifier(column):
                raise ValueError(f"非法列名：{column}")
        where, params = self._build_where(filters, keyword)
        return [
            dict(row)
            for row in self.conn.execute(
                f"SELECT {select}, COUNT(*) AS row_count FROM {self.table} "
                f"WHERE {where} GROUP BY {select}",
                params,
            ).fetchall()
        ]

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
