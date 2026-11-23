"""执行媒体（四期专项 C）。

浏览器类执行可以留下**可回放的媒体证据**：视频（.webm/.mp4）与 Trace（.zip）。
本模块只负责「落盘 + 限流 + 安全取回」，不伪造内容——执行器（MCP 浏览器）没产出媒体时
这里不写任何文件，证据里也不挂空附件。

口径：
  - 文件只落本机 `data/exec-media/<批次>/`，不进项目资产包与整包备份；
  - 单次执行视频 ≤ 50MB、Trace ≤ 20MB，超限自动降为「仅截图」并留说明；
  - 取回路径必须落在媒体根目录内（防目录穿越）。
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from ..common.secret_store import atomic_write_bytes
from ..logging_setup import log_event

logger = logging.getLogger(__name__)

MEDIA_DIR = "exec-media"
KIND_VIDEO = "video"
KIND_TRACE = "trace"
MEDIA_KINDS = (KIND_VIDEO, KIND_TRACE)

#: 单次执行的媒体上限
MAX_BYTES: dict[str, int] = {
    KIND_VIDEO: 50 * 1024 * 1024,
    KIND_TRACE: 20 * 1024 * 1024,
}

LIMIT_LABELS: dict[str, str] = {
    KIND_VIDEO: "视频 ≤ 50MB",
    KIND_TRACE: "Trace ≤ 20MB",
}

#: 后缀白名单：只收可回放的媒体，避免把任意文件塞进来
ALLOWED_SUFFIX: dict[str, tuple[str, ...]] = {
    KIND_VIDEO: (".webm", ".mp4"),
    KIND_TRACE: (".zip",),
}


def _root(conn: sqlite3.Connection) -> Path:
    """媒体根目录：与数据库同级的 data/exec-media。"""
    row = conn.execute("PRAGMA database_list").fetchone()
    db_path = Path(str(row[2])) if row is not None and row[2] else Path("aitp.db")
    return db_path.parent / MEDIA_DIR


def limit_of(kind: str) -> int:
    return int(MAX_BYTES.get(kind, 0))


def degrade_note(kind: str, size: int) -> str:
    """超限说明：写进证据日志，让「为什么没有视频」可解释。"""
    label = LIMIT_LABELS.get(kind, kind)
    return f"{kind} {size} 字节超出{label}上限，已降级为仅截图"


def save_media(
    conn: sqlite3.Connection,
    *,
    batch: str,
    kind: str,
    file_name: str,
    data: bytes,
) -> dict[str, Any] | None:
    """落盘一份媒体；超限 / 类型不符返回 None（调用方记降级说明）。"""
    if kind not in MEDIA_KINDS:
        return None
    suffix = Path(str(file_name or "")).suffix.lower()
    if suffix not in ALLOWED_SUFFIX[kind]:
        return None
    if not data:
        return None
    size = len(data)
    if size > limit_of(kind):
        return None
    safe_batch = "".join(ch for ch in str(batch or "misc") if ch.isalnum() or ch in "-_") or "misc"
    directory = _root(conn) / safe_batch
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / Path(str(file_name)).name
        atomic_write_bytes(target, data)
    except OSError as exc:
        log_event(logger, "exec_media_write_failed", level=logging.WARNING,
                  kind=kind, error=str(exc)[:200])
        return None
    return {
        "fileName": target.name,
        "filePath": str(target),
        "relativePath": f"{safe_batch}/{target.name}",
        "kind": kind,
        "size": size,
    }


def media_file(conn: sqlite3.Connection, relative: str) -> Path | None:
    """按相对路径取媒体文件；越界或不存在返回 None。"""
    raw = str(relative or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        return None
    root = _root(conn).resolve()
    target = (root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


def clear_batch(conn: sqlite3.Connection, batch: str) -> None:
    """留存到期清理：整批删除。"""
    safe_batch = "".join(ch for ch in str(batch or "") if ch.isalnum() or ch in "-_")
    if not safe_batch:
        return
    directory = _root(conn) / safe_batch
    shutil.rmtree(directory, ignore_errors=True)


def _cutoff(days: int) -> float:
    import time

    return time.time() - max(0, int(days)) * 86400


def list_expired(conn: sqlite3.Connection, days: int) -> list[str]:
    """到期的媒体批次（按目录最后修改时间判定）。"""
    root = _root(conn)
    if not root.is_dir():
        return []
    cutoff = _cutoff(days)
    out: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime <= cutoff:
                out.append(child.name)
        except OSError:
            continue
    return out


def purge_expired(conn: sqlite3.Connection, days: int) -> int:
    """删除到期批次，返回删除的批次数。"""
    removed = 0
    for batch in list_expired(conn, days):
        clear_batch(conn, batch)
        removed += 1
    return removed
