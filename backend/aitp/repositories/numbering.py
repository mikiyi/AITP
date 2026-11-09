"""业务编号生成：REQ-001 / FT-001 / BUG-001 / RPT-001。

编号只统计未软删的记录，取最小空缺号，因此软删后编号可以被重新使用。
配合的部分唯一索引（WHERE is_deleted = 0），复用编号不会与历史软删记录冲突。

并发口径（GAP-26）：「查已用号 → 算最小空缺 → 插一条」是典型的读-改-写，
多个写者并发时必然读到同一个空缺号，最后一起撞唯一索引
（实测 16 并发批量建用例，失败全是 `UNIQUE constraint failed: ... case_no`，
一条 database is locked 都没有——busy_timeout 吃掉了锁等待，却管不住事务之外的读）。
所以所有取号都先 `begin_claim()` 把读放进写事务里，取号与紧随其后的插入共用同一次持锁。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

NO_WIDTH = 3
NO_PATTERN = re.compile(r"^[A-Za-z]+-(\d+)$")

#: 事务未开时先手工开一个写事务；已经开了就沿用，重复 BEGIN 会直接报错
_ALREADY_IN_TXN = "within a transaction"


def begin_claim(conn: sqlite3.Connection) -> None:
    """把「读已用号 → 算最小空缺」锁进写事务，取号与后面的插入因此是原子的。

    为什么用 `BEGIN IMMEDIATE`，而不是复用 db/write_guard.py 的进程内写锁：
      - write_guard 串行化的是 commit/rollback 这一瞬间（见它的模块说明），
        取号发生在第一条 DML 之前，两个线程照样各自读到同一个空缺号；
      - `BEGIN IMMEDIATE` 在取号这一刻就拿住 SQLite 的写锁，别的写者要么在
        busy_timeout 内排队等到提交、要么排到之后重新读到新号，
        跨连接、跨进程都成立，不依赖「同一个进程」这个前提；
      - 已经在事务里（批量导入边插边取号、外层服务先写过）就不重复开：
        此时第一条 DML 早就拿到写锁，本函数无事可做。
    持锁时长到本次 commit/rollback 为止，而调用方的取号与插入是紧挨着的两句，
    与普通写事务同量级，不会比改造前的 INSERT 多占一刻。
    """
    if conn.in_transaction:
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        # 并发走到这里时只剩「别人刚开了事务」这一种可能，沿用对方的读快照即可
        if _ALREADY_IN_TXN not in str(exc):
            raise


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
    """返回下一个可用编号，形如 REQ-001。

    调用方必须紧接着用同一个 conn 插入并 commit/rollback，取号才算数。
    """
    begin_claim(conn)
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
    """用例编号按类型分前缀计数：FT-001 / IT-001 / BT-001 / ST-001 / UIT-001 / ET-001。

    与 next_no 同一套并发口径：先 begin_claim 把读放进写事务，再由调用方紧接插入。
    """
    begin_claim(conn)
    sql = f"SELECT {column} FROM {table} WHERE is_deleted = 0 AND project_id = ?"
    params: list[Any] = [project_id]
    if case_type:
        sql += " AND case_type = ?"
        params.append(case_type)

    used = _used_sequences(conn.execute(sql, params).fetchall(), prefix)
    return f"{prefix}-{_first_gap(used):0{NO_WIDTH}d}"
