"""SQLite 连接管理。

每个请求一条连接（flask.g），后台任务用独立连接。
所有连接统一施加 PRAGMA：WAL / 外键 / busy_timeout。

并发口径（GAP-26）：
  - journal_mode 只在还不是 WAL 时才切：切 WAL 要拿独占锁，别的写者在跑就会直接吃
    SQLITE_BUSY；库里已经是 WAL 时这一步纯属多余，省掉它就少了一类「一连上就报锁库」。
  - isolation_level = IMMEDIATE：Python 默认给写事务发 BEGIN DEFERRED，两条连接先读后写
    时会撞上「读锁升级写锁」这种死锁，SQLite 对它是**立刻**返回 database is locked、
    连 busy_timeout 都不等。改成 IMMEDIATE 后写者在第一笔 DML 就排队，忙等真的生效。
  - 提交/回滚走 write_guard 的进程内写锁：同进程多个写者不再互相撞锁，
    等待时长与慢等待次数由那里统一计数、超阈值出 WARNING（带 traceId）。
  - busy_timeout 默认仍是 5000；写密集的后台路径显式用 WRITER_BUSY_TIMEOUT_MS，
    按场景放宽而不是全局调大——全局调大只会让请求更久才失败。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, cast

from flask import current_app, g

#: 默认忙等窗口：保持 5000，与《数据层验收》里 busy_timeout = 5000 的断言一致
DEFAULT_BUSY_TIMEOUT_MS = 5000
#: 写密集场景（迁移、备份、批量执行、后台任务）的忙等窗口：这些路径本来就预期排队，
#: 等得久一点换来不报错，比让接口返回 199000 更合适
WRITER_BUSY_TIMEOUT_MS = 15000

#: 除 journal_mode 外的固定 PRAGMA：journal_mode 要按需切换，见 _apply_pragmas
DB_PRAGMAS = (
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA cache_size = -8000",
)

#: 兼容旧引用：完整 PRAGMA 清单（实际执行顺序见 _apply_pragmas）
DB_PRAGMAS_ALL = ("PRAGMA journal_mode = WAL",) + DB_PRAGMAS


class AitpConnection(sqlite3.Connection):
    """带写串行化的连接。

    只改写提交/回滚两个入口，读语句一条都不拦，所以读路径零额外开销。
    注意：`with conn:` 的事务收尾走 C 层实现，不经过这里；需要排队保证的写入
    一律显式调用 conn.commit()（API 层的 helpers.commit 已是这个形态）。
    """

    #: 连接归属的库文件绝对路径：写锁按库分，多实例/临时库互不影响
    aitp_db_path: str

    def commit(self) -> None:
        from .write_guard import commit_serialized

        commit_serialized(self, lambda: sqlite3.Connection.commit(self))

    def rollback(self) -> None:
        from .write_guard import rollback_serialized

        rollback_serialized(self, lambda: sqlite3.Connection.rollback(self))


def _apply_pragmas(conn: sqlite3.Connection, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> None:
    # busy_timeout 必须第一个落：后面任何一步要排队时，等待上限已经生效
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    for pragma in DB_PRAGMAS:
        conn.execute(pragma)
    journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0] or "").lower()
    if journal != "wal":
        conn.execute("PRAGMA journal_mode = WAL")


def _db_path_text(db_path: Path | str) -> str:
    from .write_guard import normalize_db_path

    return normalize_db_path(db_path)


def _connect(
    db_path: Path,
    *,
    busy_timeout_ms: int,
    timeout_s: float,
    check_same_thread: bool,
) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        timeout=timeout_s,
        isolation_level="IMMEDIATE",
        check_same_thread=check_same_thread,
        factory=AitpConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.aitp_db_path = _db_path_text(db_path)
    _apply_pragmas(conn, busy_timeout_ms)
    return conn


def get_db() -> sqlite3.Connection:
    """请求级连接，随应用上下文自动关闭。"""
    if "db" not in g:
        db_path = Path(current_app.config["AITP_DB_PATH"])
        g.db = _connect(
            db_path,
            busy_timeout_ms=int(current_app.config.get("AITP_BUSY_TIMEOUT_MS", DEFAULT_BUSY_TIMEOUT_MS)),
            timeout_s=5.0,
            check_same_thread=True,
        )
    return cast(sqlite3.Connection, g.db)


def close_db(exc: BaseException | None = None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def get_standalone_conn(
    db_path: Path | None = None,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """脚本与后台线程用的独立连接（不复用 flask.g）。

    busy_timeout_ms 给写密集的调用方显式放宽（迁移、备份、批量执行、后台任务）。
    """
    if db_path is None:
        db_path = Path(current_app.config["AITP_DB_PATH"])
        busy_timeout_ms = int(current_app.config.get("AITP_BUSY_TIMEOUT_MS", busy_timeout_ms))
    return _connect(
        db_path,
        busy_timeout_ms=busy_timeout_ms,
        timeout_s=max(5.0, busy_timeout_ms / 1000.0),
        check_same_thread=False,
    )



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
