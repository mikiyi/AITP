"""SQLite 连接管理。

每个请求一条连接（flask.g），后台任务用独立连接。
所有连接统一施加 PRAGMA：WAL / 外键 / busy_timeout。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

from flask import current_app, g

DB_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA cache_size = -8000",
)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    for pragma in DB_PRAGMAS:
        conn.execute(pragma)


def get_db() -> sqlite3.Connection:
    """请求级连接，随应用上下文自动关闭。"""
    if "db" not in g:
        db_path = Path(current_app.config["AITP_DB_PATH"])
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        _apply_pragmas(conn)
        g.db = conn
    return cast(sqlite3.Connection, g.db)


def close_db(exc: BaseException | None = None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def get_standalone_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """脚本与后台线程用的独立连接（不复用 flask.g）。"""
    if db_path is None:
        db_path = Path(current_app.config["AITP_DB_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn
