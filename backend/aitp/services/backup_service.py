"""本机数据库备份与恢复。

单用户本地工具的全部资产就是这一个 SQLite 文件，所以备份与数据库同级存放，
文件名固定格式、读写前统一校验，避免任意路径读写。
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import current_app

from ..db.migrator import run_migrations
from ..errors import AppError, ErrorCode

BACKUP_SUBDIR = "backups"
BACKUP_PREFIX = "aitp"
SAFETY_PREFIX = "pre-restore"
# 备份文件必须是本服务生成的名字，恢复前先确认它是 AITP 自己的库
TABLE_MARKER = "sys_schema_version"
NAME_PATTERN = re.compile(r"^(?:aitp|pre-restore)-\d{8}-\d{6}(?:-\d{2})?\.db$")


def _db_path() -> Path:
    return Path(current_app.config["AITP_DB_PATH"])


def backup_dir() -> Path:
    directory = _db_path().parent / BACKUP_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _new_name(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = backup_dir()
    candidate = f"{prefix}-{stamp}.db"
    index = 1
    while (root / candidate).exists():
        candidate = f"{prefix}-{stamp}-{index:02d}.db"
        index += 1
    return candidate


def _resolve(name: str) -> Path:
    """把入参名字收敛到备份目录内的一个合法文件路径。"""
    if not NAME_PATTERN.match(name):
        raise AppError(ErrorCode.PARAM_ERROR, "备份文件名不合法")
    root = backup_dir().resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root):
        raise AppError(ErrorCode.PARAM_ERROR, "备份文件名不合法")
    return target


def resolve_existing(name: str) -> Path:
    path = _resolve(name)
    if not path.exists():
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "备份文件不存在")
    return path


def describe(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "sizeBytes": stat.st_size,
        "createdAt": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "isSafety": path.name.startswith(SAFETY_PREFIX),
    }


def create(conn: sqlite3.Connection, prefix: str = BACKUP_PREFIX) -> dict[str, Any]:
    """用 VACUUM INTO 产出单文件快照，不产生 -wal/-shm 附属文件。"""
    target = _resolve(_new_name(prefix))
    conn.commit()
    conn.execute("VACUUM INTO ?", (str(target),))
    return describe(target)


def list_backups() -> list[dict[str, Any]]:
    items = [describe(path) for path in backup_dir().glob("*.db") if NAME_PATTERN.match(path.name)]
    return sorted(items, key=lambda item: str(item["name"]), reverse=True)


def remove(name: str) -> str:
    path = resolve_existing(name)
    try:
        path.unlink()
    except OSError as exc:
        # Windows 上刚下载完的文件可能仍被系统短暂占用，这里给可重试的提示而不是 500
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "备份文件暂时无法删除，请稍后重试") from exc
    return path.name


def _validate(path: Path) -> None:
    uri = f"file:{path.as_posix()}?mode=ro"
    # with sqlite3.connect(...) 只管事务不管关闭，Windows 上会一直占着备份文件
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if row is None or str(row[0]).lower() != "ok":
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "备份文件完整性校验失败，不能用于恢复")
        tables = {
            str(item[0])
            for item in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if TABLE_MARKER not in tables:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "备份文件不是 AITP 数据库，不能用于恢复")


def restore(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """就地覆盖当前库内容，恢复前先自动存一份当前数据。

    用 SQLite 备份 API 而不是替换文件：Windows 上活文件被服务进程占用时无法删除，
    而覆盖内容不需要动文件名，服务不用重启即可读到恢复后的数据。
    """
    source = resolve_existing(name)
    _validate(source)
    safety = create(conn, prefix=SAFETY_PREFIX)
    conn.commit()
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with contextlib.closing(sqlite3.connect(source_uri, uri=True)) as src:
        src.backup(conn)
    conn.commit()
    applied = run_migrations(conn)
    conn.commit()
    return {
        "restored": source.name,
        "safetyBackup": str(safety["name"]),
        "appliedMigrations": applied,
    }
