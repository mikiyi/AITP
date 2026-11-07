"""本机数据库备份与恢复。

单用户本地工具的全部资产不止一个 SQLite 文件：附件、生成的脚本、知识库
Markdown、密钥都在 data / knowledge 目录里。所以有两种备份：
- `.db` 快照（VACUUM INTO）：轻量、日常回滚用；
- `.zip` 整包：库 + 上述目录，恢复后不会出现「附件全是孤儿」。
文件名固定格式、读写前统一校验，避免任意路径读写。
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import current_app

from ..db.migrator import migration_files, migration_version, run_migrations
from ..errors import AppError, ErrorCode

BACKUP_SUBDIR = "backups"
BACKUP_PREFIX = "aitp"
SAFETY_PREFIX = "pre-restore"
UPLOAD_PREFIX = "upload"
# 备份文件必须是本服务生成的名字，恢复前先确认它是 AITP 自己的库
TABLE_MARKER = "sys_schema_version"
NAME_PATTERN = re.compile(r"^(?:aitp|pre-restore|upload)-\d{8}-\d{6}(?:-\d{2})?\.db$")
#: 允许上传还原的文件后缀与大小上限
UPLOAD_SUFFIXES = (".db", ".sqlite", ".sqlite3")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

BUNDLE_PREFIX = "bundle"
PRE_BUNDLE_PREFIX = "pre-bundle"
AUTO_PREFIX = "auto"
BUNDLE_NAME_PATTERN = re.compile(
    r"^(?:bundle|pre-bundle|auto)-\d{8}-\d{6}(?:-\d{2})?\.zip$"
)
BUNDLE_DB_NAME = "aitp.db"
BUNDLE_MANIFEST_NAME = "manifest.json"
BUNDLE_KNOWLEDGE_DIR = "knowledge"
#: 库里只存引用，正文、凭据与模板原文件在这些目录里，缺了就无法完整恢复
BUNDLE_ASSET_DIRS = ("attachments", "automation", "generated", "secrets", "doc_templates")


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
        "kind": "db",
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


def save_upload(data: bytes, filename: str) -> dict[str, Any]:
    """把用户上传的备份文件收进备份目录并校验，供后续恢复使用。

    先写临时文件再原子改名，校验不过就删掉，不留下半截文件。
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise AppError(ErrorCode.PARAM_ERROR, "只支持 .db / .sqlite 备份文件")
    if not data:
        raise AppError(ErrorCode.PARAM_ERROR, "备份文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError(ErrorCode.PARAM_ERROR, "备份文件不能超过 200MB")

    target = _resolve(_new_name(UPLOAD_PREFIX))
    temp = target.with_name(target.name + ".uploading")
    temp.write_bytes(data)
    temp.replace(target)
    try:
        _validate(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return describe(target)


def remove(name: str) -> str:
    path = (
        resolve_existing_bundle(name)
        if name.endswith(".zip")
        else resolve_existing(name)
    )
    try:
        path.unlink()
    except OSError as exc:
        # Windows 上刚下载完的文件可能仍被系统短暂占用，这里给可重试的提示而不是 500
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "备份文件暂时无法删除，请稍后重试") from exc
    return path.name


def _validate(path: Path) -> None:
    uri = f"file:{path.as_posix()}?mode=ro"
    # with sqlite3.connect(...) 只管事务不管关闭，Windows 上会一直占着备份文件
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError as exc:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "备份文件不是有效的 SQLite 数据库") from exc
    with contextlib.closing(conn) as conn:
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            tables = {
                str(item[0])
                for item in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        except sqlite3.DatabaseError as exc:
            raise AppError(
                ErrorCode.ILLEGAL_OPERATION, "备份文件不是有效的 SQLite 数据库",
            ) from exc
        if row is None or str(row[0]).lower() != "ok":
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "备份文件完整性校验失败，不能用于恢复")
        if TABLE_MARKER not in tables:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "备份文件不是 AITP 数据库，不能用于恢复")
        row = conn.execute("SELECT MAX(version) FROM sys_schema_version").fetchone()
        source_version = int(row[0] or 0) if row else 0
        latest_known = max((migration_version(p) for p in migration_files()), default=0)
        if source_version > latest_known:
            raise AppError(
                ErrorCode.ILLEGAL_OPERATION,
                f"备份来自更高版本（schema {source_version}），当前程序仅支持到 schema {latest_known}，请升级后再恢复",
            )


def restore(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """就地覆盖当前库内容，恢复前先自动存一份当前数据。

    用 SQLite 备份 API 而不是替换文件：Windows 上活文件被服务进程占用时无法删除，
    而覆盖内容不需要动文件名，服务不用重启即可读到恢复后的数据。
    """
    source = resolve_existing(name)
    _validate(source)
    safety = create(conn, prefix=SAFETY_PREFIX)
    applied = _restore_db(conn, source)
    return {
        "restored": source.name,
        "safetyBackup": str(safety["name"]),
        "appliedMigrations": applied,
    }


def _restore_db(conn: sqlite3.Connection, source: Path) -> list[int]:
    """把一个已经校验过的库文件覆盖进当前库，返回补应用的结构版本。"""
    conn.commit()
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with contextlib.closing(sqlite3.connect(source_uri, uri=True)) as src:
        src.backup(conn)
    conn.commit()
    applied = run_migrations(conn)
    conn.commit()
    return applied


# ---------------------------------------------------------------------
# 整包备份：库 + 附件 / 脚本 / 生成物 / 密钥 / 知识库 Markdown
# ---------------------------------------------------------------------

def _data_root() -> Path:
    return _db_path().parent


def _new_bundle_name(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = backup_dir()
    candidate = f"{prefix}-{stamp}.zip"
    index = 1
    while (root / candidate).exists():
        candidate = f"{prefix}-{stamp}-{index:02d}.zip"
        index += 1
    return candidate


def _resolve_bundle(name: str) -> Path:
    if not BUNDLE_NAME_PATTERN.match(name):
        raise AppError(ErrorCode.PARAM_ERROR, "整包备份文件名不合法")
    root = backup_dir().resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root):
        raise AppError(ErrorCode.PARAM_ERROR, "整包备份文件名不合法")
    return target


def resolve_existing_bundle(name: str) -> Path:
    path = _resolve_bundle(name)
    if not path.is_file():
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "整包备份不存在")
    return path


def describe_bundle(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "sizeBytes": stat.st_size,
        "createdAt": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "isSafety": path.name.startswith(PRE_BUNDLE_PREFIX),
        "isAuto": path.name.startswith(f"{AUTO_PREFIX}-"),
        "kind": "bundle",
    }


def _add_tree(zf: zipfile.ZipFile, base: Path, arc_prefix: str) -> int:
    """把目录里的文件按 `arc_prefix/相对路径` 打进包，空目录跳过。"""
    if not base.is_dir():
        return 0
    count = 0
    for item in sorted(base.rglob("*")):
        if not item.is_file():
            continue
        zf.write(item, f"{arc_prefix}/{item.relative_to(base).as_posix()}")
        count += 1
    return count


def _snapshot_path(prefix: str) -> Path:
    """整包里的库快照先落到备份目录，打包完即删。

    名字带前导点与 .snapshot.db 后缀，故意不匹配两个列表 pattern，
    免得中途崩溃留下的半成品被当成一份可恢复的备份。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return backup_dir() / f".{prefix}-{stamp}-{uuid.uuid4().hex[:8]}.snapshot.db"


def create_bundle(conn: sqlite3.Connection, prefix: str = BUNDLE_PREFIX) -> dict[str, Any]:
    """产出整包：库快照 + 正文与凭据目录，一次拿走全部资产。"""
    from . import knowledge_storage

    target = _resolve_bundle(_new_bundle_name(prefix))
    temp = target.with_name(target.name + ".writing")
    snapshot = _snapshot_path(prefix)
    knowledge_root = knowledge_storage.storage_root(conn)
    data_root = _data_root()

    conn.commit()
    conn.execute("VACUUM INTO ?", (str(snapshot),))
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, BUNDLE_DB_NAME)
            files = 1
            members: dict[str, int] = {}
            for name in BUNDLE_ASSET_DIRS:
                members[name] = _add_tree(zf, data_root / name, name)
                files += members[name]
            members[BUNDLE_KNOWLEDGE_DIR] = _add_tree(zf, knowledge_root, BUNDLE_KNOWLEDGE_DIR)
            files += members[BUNDLE_KNOWLEDGE_DIR]
            zf.writestr(
                BUNDLE_MANIFEST_NAME,
                json.dumps(
                    {
                        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "files": files,
                        "members": members,
                        "knowledgeRoot": str(knowledge_root),
                    },
                    ensure_ascii=False,
                ),
            )
        temp.replace(target)
    finally:
        snapshot.unlink(missing_ok=True)
        temp.unlink(missing_ok=True)
    info = describe_bundle(target)
    info["files"] = files
    return info


def list_bundles() -> list[dict[str, Any]]:
    items = [
        describe_bundle(path)
        for path in backup_dir().glob("*.zip")
        if BUNDLE_NAME_PATTERN.match(path.name)
    ]
    return sorted(items, key=lambda item: str(item["name"]), reverse=True)


def list_auto_bundles() -> list[dict[str, Any]]:
    return [item for item in list_bundles() if item.get("isAuto")]


def prune_auto(keep: int) -> list[str]:
    """只清理最旧的自动整包，保留手动整包与恢复前自动整包。

    删除失败（例如文件被系统短暂占用）时跳过，不影响本次备份结果。
    """
    files = sorted(
        (
            path
            for path in backup_dir().glob(f"{AUTO_PREFIX}-*.zip")
            if BUNDLE_NAME_PATTERN.match(path.name)
        ),
        key=lambda path: path.name,
    )
    excess = len(files) - max(0, keep)
    removed: list[str] = []
    for path in files[: max(0, excess)]:
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path.name)
    return removed


def _checked_members(zf: zipfile.ZipFile) -> None:
    """解压前逐个检查成员名：整包可能被手工改过，不能让 ../ 写到大本营外面。"""
    names = [str(item.filename) for item in zf.infolist()]
    if BUNDLE_DB_NAME not in names:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "整包备份里缺少数据库文件")
    for name in names:
        normalized = name.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if normalized.startswith("/") or ".." in parts:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, f"整包备份里有非法路径：{name}")


def _replace_tree(source: Path, target: Path) -> int:
    """用备份里的目录整体替换现有目录，返回替换的文件数。"""
    if not source.is_dir():
        return 0
    files = sum(1 for item in source.rglob("*") if item.is_file())
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return files


def restore_bundle(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """整包恢复：先给当前库和当前目录各存一份安全副本，再逐项覆盖。"""
    from . import knowledge_storage

    path = resolve_existing_bundle(name)
    workdir = Path(tempfile.mkdtemp(prefix="bundle-", dir=str(backup_dir())))
    try:
        with zipfile.ZipFile(path) as zf:
            _checked_members(zf)
            zf.extractall(workdir)

        source_db = workdir / BUNDLE_DB_NAME
        _validate(source_db)

        safety_backup = create(conn, prefix=SAFETY_PREFIX)
        safety_bundle = create_bundle(conn, prefix=PRE_BUNDLE_PREFIX)

        applied = _restore_db(conn, source_db)

        data_root = _db_path().parent
        restored_files: dict[str, int] = {}
        for asset in BUNDLE_ASSET_DIRS:
            count = _replace_tree(workdir / asset, data_root / asset)
            if count:
                restored_files[asset] = count
        knowledge_files = _replace_tree(
            workdir / BUNDLE_KNOWLEDGE_DIR, knowledge_storage.storage_root(conn)
        )
        if knowledge_files:
            restored_files[BUNDLE_KNOWLEDGE_DIR] = knowledge_files
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return {
        "restored": path.name,
        "safetyBackup": str(safety_backup["name"]),
        "safetyBundle": str(safety_bundle["name"]),
        "appliedMigrations": applied,
        "restoredFiles": restored_files,
    }
