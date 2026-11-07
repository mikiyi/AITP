"""门禁脚本用的数据库快照：把正式库复制成一份临时库，验证写在临时库上做。

AITP 是单机工具，跑门禁时正式库往往正被服务进程用 WAL 写着。直接 copy 文件可能拿到
半截 WAL，所以用 SQLite 备份 API 取一致性副本；连接期间开 query_only，保证不会往正式库
写一个字节。备份 API 不可用（库被独占锁死等）时回退到文件复制。
"""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE_DB = ROOT / "data" / "aitp.db"


def _copy_with_sidecars(source: Path, target: Path) -> None:
    shutil.copy2(source, target)
    for suffix in ("-wal", "-shm"):
        sidecar = source.with_name(source.name + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, target.with_name(target.name + suffix))


def snapshot(source: Path, target: Path) -> Path:
    """把 source 库一致性复制到 target，返回 target 路径。"""
    if not source.exists():
        raise FileNotFoundError(f"数据库不存在: {source}")
    try:
        with contextlib.closing(sqlite3.connect(source, timeout=5.0)) as src:
            src.execute("PRAGMA query_only = ON")
            src.execute("PRAGMA busy_timeout = 5000")
            with contextlib.closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
    except sqlite3.Error:
        _copy_with_sidecars(source, target)
    return target
