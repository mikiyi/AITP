"""SQLite 写路径加固（GAP-26）：进程内写串行化 + 锁等待计数 + busy 重试。

分工要分清，别把这里当成「跨进程锁」：
  - 跨进程/跨连接的写互斥由 SQLite 负责（WAL + busy_timeout + BEGIN IMMEDIATE，见 db/connection.py）；
  - 本模块负责同一进程内的写者排队：把「提交」这一步在进程内串行起来，
    并把等待次数、等待时长、busy 重试次数计下来；等待超过阈值就打一条带 traceId 的 WARNING。

为什么只串行化提交、不串行化整条写事务：
  事务跨度在业务层（第一条 DML 到 commit），把进程内锁按住整个跨度意味着
  「异常路径忘记释放」会直接让所有写者永久卡死；而提交是唯一必须互斥的瞬间
  （提交才真正把写锁交接与 WAL 落盘做完）。事务期间的互斥交给 SQLite 的忙等，
  它的等待有 busy_timeout 上限，不会无限期拖住别人。

读路径完全不经过这里：只读连接与只读语句不加任何锁，读性能不受影响。
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from ..logging_setup import current_trace_id, log_event

logger = logging.getLogger("aitp.db.write")

#: 写者等待超过这个毫秒数就出 WARNING：本地单用户库里正常提交是亚毫秒级，
#: 能等到几百毫秒说明有长事务或外部进程在压着写锁，属于需要留痕的一类。
SLOW_WAIT_MS = 150

#: 拿到 SQLITE_BUSY 后的重试次数与间隔。忙等已经由 busy_timeout 在 SQLite 内部做过，
#: 这里只兜「忙等窗口刚好用完」的边界情况，所以次数少、间隔短。
BUSY_RETRY_LIMIT = 3
BUSY_RETRY_SLEEP_S = 0.05

#: 等进程内写锁的上限（秒）。超过它就是真有事务卡死，宁可报错也不要无限期挂住请求。
WRITE_LOCK_TIMEOUT_S = 30.0

_BUSY_MARKS = ("database is locked", "database is write locked")

_REGISTRY_LOCK = threading.Lock()
_WRITER_LOCKS: dict[str, threading.RLock] = {}
#: 后台线程（流式生产者、任务队列）没有 flask 请求上下文，用线程局部的追踪号补位
_TRACE_HINT = threading.local()


class WriteLockTimeoutError(sqlite3.OperationalError):
    """进程内写锁等待超时。

    刻意继承 sqlite3.OperationalError：上层原本就在按 sqlite3.Error 分类处理写库失败，
    不需要为这一种新异常再开一条分支。
    """


class _Stats:
    """写锁运行计数；只增不减，读快照时加锁取一次。"""

    __slots__ = ("_lock", "commits", "waits", "slow_waits", "busy_errors", "busy_retries",
                 "timeouts", "total_wait_ms", "max_wait_ms")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.commits = 0
        self.waits = 0
        self.slow_waits = 0
        self.busy_errors = 0
        self.busy_retries = 0
        self.timeouts = 0
        self.total_wait_ms = 0.0
        self.max_wait_ms = 0.0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "commits": self.commits,
                "waits": self.waits,
                "slowWaits": self.slow_waits,
                "busyErrors": self.busy_errors,
                "busyRetries": self.busy_retries,
                "timeouts": self.timeouts,
                "totalWaitMs": round(self.total_wait_ms, 2),
                "maxWaitMs": round(self.max_wait_ms, 2),
            }

    def committed(self) -> None:
        with self._lock:
            self.commits += 1

    def waited(self, wait_ms: float, busy_errors: int, retries: int, timed_out: bool) -> bool:
        """累加等待计数；返回是否为慢等待（需要出 WARNING）。"""
        with self._lock:
            if timed_out:
                self.timeouts += 1
            if wait_ms > 0:
                self.waits += 1
                self.total_wait_ms += wait_ms
                if wait_ms > self.max_wait_ms:
                    self.max_wait_ms = wait_ms
            if busy_errors:
                self.busy_errors += busy_errors
            if retries:
                self.busy_retries += retries
            slow = wait_ms >= SLOW_WAIT_MS or timed_out
            if slow:
                self.slow_waits += 1
            return slow


_STATS = _Stats()


def normalize_db_path(db_path: Path | str | sqlite3.Connection) -> str:
    """把「哪个库文件」归一化成锁的键；同一文件的相对/绝对写法落到同一把锁。"""
    if isinstance(db_path, sqlite3.Connection):
        hinted = getattr(db_path, "aitp_db_path", "")
        if isinstance(hinted, str) and hinted:
            return hinted
        return ":memory:"
    text = str(db_path)
    if text in (":memory:", ""):
        return text or ":memory:"
    try:
        return str(Path(text).resolve())
    except OSError:
        # 路径规范化失败（非法字符、符号链接环）时按原样加锁：宁可多一把锁也不丢锁
        return text


def writer_lock(db_path: Path | str | sqlite3.Connection) -> threading.RLock:
    """取（并按需创建）某个库文件的进程内写锁。"""
    key = normalize_db_path(db_path)
    with _REGISTRY_LOCK:
        lock = _WRITER_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WRITER_LOCKS[key] = lock
        return lock


def trace_hint(trace_id: str | None = None) -> None:
    """后台线程设置/清空本线程的追踪号，让写锁日志也能挂到发起它的那次请求上。"""
    _TRACE_HINT.value = (trace_id or "").strip()[:64]


def _trace_id() -> str:
    hint = getattr(_TRACE_HINT, "value", "")
    if hint:
        return str(hint)
    return current_trace_id()


def log_trace_id() -> str:
    """给业务日志用的追踪号：后台线程取 trace_hint 设的值，请求线程取 g.trace_id。

    流式生产者跑在独立线程池里，没有请求上下文；
    它提交任务时把发起请求的 traceId 交给 `trace_hint`，日志列才不会变成占位符。
    """
    return _trace_id()


def _is_busy(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(mark in text for mark in _BUSY_MARKS)


@contextlib.contextmanager
def write_guard(
    db_path: Path | str | sqlite3.Connection,
    *,
    op: str = "write",
    timeout_s: float = WRITE_LOCK_TIMEOUT_S,
) -> Iterator[None]:
    """进程内写串行化：拿锁、计量等待、慢等待留 WARNING。

    可重入（RLock）：同一条连接/同一个线程在写事务里再提交一次不会自己锁死自己。
    """
    lock = writer_lock(db_path)
    started = time.perf_counter()
    waited = not lock.acquire(blocking=False)
    if waited:
        if not lock.acquire(timeout=max(0.001, timeout_s)):
            wait_ms = (time.perf_counter() - started) * 1000
            _STATS.waited(wait_ms, 0, 0, True)
            log_event(
                logger, "db_write_timeout", level=logging.ERROR,
                traceId=_trace_id(), op=op, db=Path(normalize_db_path(db_path)).name,
                waitMs=round(wait_ms, 1), timeoutS=round(timeout_s, 1),
            )
            raise WriteLockTimeoutError(
                f"等待数据库写锁超过 {timeout_s:.0f}s（操作 {op}）；"
                "有事务长时间持有写锁未提交，请检查批量执行/后台任务"
            )
    wait_ms = (time.perf_counter() - started) * 1000
    try:
        if wait_ms > 0:
            slow = _STATS.waited(wait_ms, 0, 0, False)
            if slow:
                log_event(
                    logger, "db_write_wait", level=logging.WARNING,
                    traceId=_trace_id(), op=op, waitMs=round(wait_ms, 1),
                    thresholdMs=SLOW_WAIT_MS, db=Path(normalize_db_path(db_path)).name,
                    slowWaits=_STATS.slow_waits,
                )
        yield
    finally:
        lock.release()


def write_stats() -> dict[str, Any]:
    """写锁计数快照，给健康检查与门禁断言用。"""
    with _REGISTRY_LOCK:
        registered = len(_WRITER_LOCKS)
    snapshot = _STATS.snapshot()
    snapshot["registeredDbs"] = registered
    snapshot["slowWaitThresholdMs"] = SLOW_WAIT_MS
    snapshot["busyRetryLimit"] = BUSY_RETRY_LIMIT
    return snapshot


def reset_write_stats() -> None:
    """清空计数：只给门禁脚本用，让每条断言从干净的计数开始。"""
    with _STATS._lock:  # noqa: SLF001 - 同模块内部的状态复位
        _STATS.commits = 0
        _STATS.waits = 0
        _STATS.slow_waits = 0
        _STATS.busy_errors = 0
        _STATS.busy_retries = 0
        _STATS.timeouts = 0
        _STATS.total_wait_ms = 0.0
        _STATS.max_wait_ms = 0.0


def run_serialized(
    db_path: Path | str | sqlite3.Connection,
    action: Callable[[], None],
    *,
    op: str = "commit",
    retries: int = BUSY_RETRY_LIMIT,
) -> int:
    """在进程内写锁下执行一次写动作，busy 时短退避重试。

    返回实际尝试次数（1 表示一次成功）。调用方需要自己保证 action 可重复执行：
    commit/rollback 天然满足。
    """
    attempts = 0
    busy_hits = 0
    with write_guard(db_path, op=op):
        while True:
            attempts += 1
            try:
                action()
            except sqlite3.OperationalError as exc:
                if not _is_busy(exc):
                    raise
                busy_hits += 1
                if attempts > retries:
                    log_event(
                        logger, "db_write_busy", level=logging.ERROR,
                        traceId=_trace_id(), op=op, attempts=attempts,
                        error=str(exc), db=Path(normalize_db_path(db_path)).name,
                    )
                    _STATS.waited(0.0, busy_hits, attempts - 1, False)
                    raise
                log_event(
                    logger, "db_write_busy", level=logging.WARNING,
                    traceId=_trace_id(), op=op, attempt=attempts, retrying=True,
                    error=str(exc), db=Path(normalize_db_path(db_path)).name,
                )
                time.sleep(BUSY_RETRY_SLEEP_S * attempts)
            else:
                _STATS.waited(0.0, busy_hits, attempts - 1, False)
                if op == "commit":
                    _STATS.committed()
                return attempts


def commit_serialized(conn: sqlite3.Connection, commit: Callable[[], None]) -> None:
    """连接提交：串行化 + busy 重试的唯一入口。"""
    run_serialized(conn, commit, op="commit")


def rollback_serialized(conn: sqlite3.Connection, rollback: Callable[[], None]) -> None:
    """连接回滚：同样排队，避免回滚与别人的提交抢 WAL 收尾。"""
    run_serialized(conn, rollback, op="rollback")
