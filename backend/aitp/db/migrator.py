"""数据库迁移执行器：按文件名顺序应用 db/migrations/*.sql，逐条记录版本。"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from ..common.time_utils import utcnow_iso

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
APP_VERSION = "1.0.0"


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sys_schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL,
            checksum    TEXT,
            app_version TEXT
        )
        """
    )


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    _ensure_version_table(conn)
    rows = conn.execute("SELECT version FROM sys_schema_version").fetchall()
    return {int(r["version"]) for r in rows}


def applied_checksums(conn: sqlite3.Connection) -> dict[int, str]:
    _ensure_version_table(conn)
    rows = conn.execute("SELECT version, checksum FROM sys_schema_version").fetchall()
    return {int(r["version"]): str(r["checksum"] or "") for r in rows}


def migration_version(path: Path) -> int:
    """版本号取文件名前缀，不取「目录里第几个文件」。

    按下标编号时，中间插一个或改一个文件名，整库的版本映射就集体错位，
    迁移会被跳过或被重复应用。
    """
    prefix = path.name.split("_", 1)[0]
    if not prefix.isdigit():
        raise ValueError(f"迁移文件名必须以序号开头，如 024_xxx.sql：{path.name}")
    return int(prefix)


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def drifted_versions(conn: sqlite3.Connection, directory: Path) -> list[int]:
    """已应用但内容被改过的迁移版本（只报告，不阻断启动）。"""
    recorded = applied_checksums(conn)
    drift: list[int] = []
    for path in sorted(directory.glob("*.sql")):
        version = migration_version(path)
        stored = recorded.get(version)
        if stored and stored != _checksum(path.read_text(encoding="utf-8")):
            drift.append(version)
    return drift


def run_migrations(
    conn: sqlite3.Connection, migrations_dir: Path | None = None
) -> list[int]:
    """应用所有未执行的迁移，返回本次应用的版本号列表。"""
    directory = migrations_dir or MIGRATIONS_DIR
    _ensure_version_table(conn)
    done = applied_versions(conn)
    applied: list[int] = []

    for path in sorted(directory.glob("*.sql")):
        version = migration_version(path)
        if version in done:
            continue
        sql = path.read_text(encoding="utf-8")
        with conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO sys_schema_version (version, applied_at, checksum, app_version) VALUES (?, ?, ?, ?)",
                (version, utcnow_iso(), _checksum(sql), APP_VERSION),
            )
        applied.append(version)
    return applied


def init_db(db_path: Path, migrations_dir: Path | None = None) -> list[int]:
    """新建空库并应用全部迁移。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        return run_migrations(conn, migrations_dir)
    finally:
        conn.close()
