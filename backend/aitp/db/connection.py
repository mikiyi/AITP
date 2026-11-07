"""SQLite 连接管理。

每个请求一条连接（flask.g），后台任务用独立连接。
所有连接统一施加 PRAGMA：WAL / 外键 / busy_timeout。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, cast

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


def database_status(db_path: Path) -> dict[str, Any]:
    """本机 SQLite 连通性快照，给顶栏状态灯与健康检查用。

    文件不存在时直接判定未连接，不调用 connect：SQLite 会顺手建出空库，
    健康检查不该有这种写副作用。
    """
    path = Path(db_path)
    started = time.perf_counter()
    info: dict[str, Any] = {
        "ok": False,
        "path": str(path),
        "exists": path.exists(),
        "sizeBytes": 0,
        "version": 0,
        "journalMode": "",
        "latencyMs": 0.0,
        "error": "",
    }
    if not path.exists():
        info["error"] = "数据库文件不存在"
        info["latencyMs"] = round((time.perf_counter() - started) * 1000, 2)
        return info

    info["sizeBytes"] = path.stat().st_size
    try:
        conn = sqlite3.connect(path, timeout=1.0)
        try:
            version = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM sys_schema_version"
            ).fetchone()[0]
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        info["error"] = str(exc)
    else:
        info["ok"] = True
        info["version"] = int(version or 0)
        info["journalMode"] = str(journal or "").lower()
    info["latencyMs"] = round((time.perf_counter() - started) * 1000, 2)
    return info
