"""业务编号生成：REQ-001 / FT-001 / BUG-001 / RPT-001。

编号只统计未软删的记录，取最小空缺号，因此软删后编号可以被重新使用。
配合的部分唯一索引（WHERE is_deleted = 0），复用编号不会与历史软删记录冲突。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

NO_WIDTH = 3
NO_PATTERN = re.compile(r"^[A-Za-z]+-(\d+)$")


def _used_sequences(rows: list[Any], prefix: str) -> set[int]:
    used: set[int] = set()
    for row in rows:
        value = str(row[0] or "")
        match = NO_PATTERN.match(value)
        if not match:
            continue
        prefix_part, seq = value.rsplit("-", 1)
        if prefix_part.upper() != prefix.upper():
            continue
        used.add(int(seq))
    return used


def _first_gap(used: set[int]) -> int:
    seq = 1
    while seq in used:
        seq += 1
    return seq


def next_no(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    prefix: str,
    project_id: int | None = None,
) -> str:
    """返回下一个可用编号，形如 REQ-001。"""
    sql = f"SELECT {column} FROM {table} WHERE is_deleted = 0"
    params: list[Any] = []
    if project_id is not None:
        sql += " AND project_id = ?"
        params.append(project_id)

    used = _used_sequences(conn.execute(sql, params).fetchall(), prefix)
    return f"{prefix}-{_first_gap(used):0{NO_WIDTH}d}"


def next_case_no(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    project_id: int,
    prefix: str,
    case_type: str | None = None,
) -> str:
    """用例编号按类型分前缀计数：FT-001 / IT-001 / BT-001 / ST-001 / UIT-001 / ET-001。"""
    sql = f"SELECT {column} FROM {table} WHERE is_deleted = 0 AND project_id = ?"
    params: list[Any] = [project_id]
    if case_type:
        sql += " AND case_type = ?"
        params.append(case_type)

    used = _used_sequences(conn.execute(sql, params).fetchall(), prefix)
    return f"{prefix}-{_first_gap(used):0{NO_WIDTH}d}"
