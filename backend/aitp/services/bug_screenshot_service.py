"""缺陷截图：文件落 data/bug-screenshots/<bugNo>/pic_<bugNo>_<seq:03d>.<ext>。

约束：
- 每个缺陷最多 30 张、单张不超过 10MB；只收常见位图（不收 SVG/HTML，避免脚本注入）；
- 序号只增不复用（软删记录也占号），保证截图文件名永远不与历史文件冲突；
- 登记表 biz_bug_screenshot 是索引，文件是正文；写入先落临时文件再原子替换。
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.numbering import begin_claim

SCREENSHOT_DIR = "bug-screenshots"
MAX_BYTES = 10 * 1024 * 1024
MAX_PER_BUG = 30
ALLOWED_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
_SAFE_NO = re.compile(r"^[A-Za-z0-9_-]+$")


def _db_dir(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = str((row[2] if row else "") or "")
    return Path(path).parent if path else Path("data")


def root_dir(conn: sqlite3.Connection) -> Path:
    root = _db_dir(conn) / SCREENSHOT_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_bug_no(bug_no: str) -> str:
    value = str(bug_no or "").strip()
    if not _SAFE_NO.match(value):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "缺陷编号不合法，无法生成截图目录")
    return value


def bug_dir(conn: sqlite3.Connection, bug_no: str) -> Path:
    target = root_dir(conn) / _safe_bug_no(bug_no)
    target.mkdir(parents=True, exist_ok=True)
    return target


def detect_image(data: bytes) -> tuple[str, str]:
    """按文件头识别图片类型：返回 (mime, 扩展名)；不是支持的图片直接拒绝。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif", ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data.startswith(b"BM"):
        return "image/bmp", ".bmp"
    raise AppError(ErrorCode.PARAM_ERROR,
                   "只支持 PNG / JPEG / GIF / WebP / BMP 截图（不接受 SVG/HTML 等可执行内容）")


def _row_public(row: dict[str, Any]) -> dict[str, Any]:
    bug_id = int(row.get("bug_id") or 0)
    shot_id = int(row.get("id") or 0)
    return {
        "id": shot_id,
        "bugId": bug_id,
        "seq": int(row.get("seq") or 0),
        "fileName": row.get("file_name") or "",
        "originalName": row.get("original_name") or "",
        "mime": row.get("mime") or "",
        "sizeBytes": int(row.get("size_bytes") or 0),
        "sha256": row.get("sha256") or "",
        "createdAt": row.get("created_at") or "",
        "url": f"/api/v1/bugs/{bug_id}/screenshots/{shot_id}/file",
    }


def list_screenshots(conn: sqlite3.Connection, bug_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM biz_bug_screenshot WHERE bug_id = ? AND is_deleted = 0"
        " ORDER BY seq",
        (int(bug_id),),
    ).fetchall()
    return [_row_public(dict(row)) for row in rows]


def get_screenshot(conn: sqlite3.Connection, bug_id: int, shot_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM biz_bug_screenshot WHERE id = ? AND bug_id = ? AND is_deleted = 0",
        (int(shot_id), int(bug_id)),
    ).fetchone()
    return dict(row) if row else None


def file_path(conn: sqlite3.Connection, row: dict[str, Any]) -> Path:
    return root_dir(conn) / _safe_bug_no(str(row.get("bug_no") or "")) / str(row.get("file_name") or "")


def _next_seq(conn: sqlite3.Connection, bug_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM biz_bug_screenshot WHERE bug_id = ?",
        (int(bug_id),),
    ).fetchone()
    return int(row[0] or 0) + 1


def save_screenshots(
    conn: sqlite3.Connection,
    bug: dict[str, Any],
    files: list[tuple[str, bytes]],
) -> list[dict[str, Any]]:
    """批量保存截图；任一文件非法则整体不落盘。调用方负责 commit。"""
    if not files:
        raise AppError(ErrorCode.PARAM_ERROR, "请选择要上传的截图")
    # 序号是「读最大值 → +1」，与取号同一类读-改-写；并发上传同一缺陷时必须串行，
    # 否则两个请求会算出同一个 seq，后到的那个撞唯一索引并覆盖前者的图片文件。
    begin_claim(conn)
    bug_id = int(bug["id"])
    existing = conn.execute(
        "SELECT COUNT(*) FROM biz_bug_screenshot WHERE bug_id = ? AND is_deleted = 0",
        (bug_id,),
    ).fetchone()[0]
    if int(existing) + len(files) > MAX_PER_BUG:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"每个缺陷最多 {MAX_PER_BUG} 张截图（当前已有 {int(existing)} 张）",
        )

    folder = bug_dir(conn, str(bug.get("bug_no") or ""))
    now = utcnow_iso()
    seq = _next_seq(conn, bug_id)
    saved_paths: list[Path] = []
    created: list[dict[str, Any]] = []
    try:
        for original_name, data in files:
            if len(data) > MAX_BYTES:
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    f"截图「{original_name}」超过 {MAX_BYTES // (1024 * 1024)}MB 限制",
                )
            mime, suffix = detect_image(data)
            while True:
                file_name = f"pic_{bug['bug_no']}_{seq:03d}{suffix}"
                target = folder / file_name
                if not target.exists():
                    break
                seq += 1
            temp = folder / f".{file_name}.writing"
            with open(temp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(target)
            saved_paths.append(target)
            cursor = conn.execute(
                "INSERT INTO biz_bug_screenshot"
                " (bug_id, seq, file_name, original_name, mime, size_bytes, sha256,"
                "  created_at, is_deleted)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    bug_id, seq, file_name, str(original_name)[:200], mime,
                    len(data), hashlib.sha256(data).hexdigest(), now,
                ),
            )
            created.append({
                "id": int(cursor.lastrowid or 0),
                "bugId": bug_id,
                "seq": seq,
                "fileName": file_name,
                "originalName": str(original_name)[:200],
                "mime": mime,
                "sizeBytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "createdAt": now,
                "url": f"/api/v1/bugs/{bug_id}/screenshots/{int(cursor.lastrowid or 0)}/file",
            })
            seq += 1
    except Exception:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise
    return created


def delete_screenshot(conn: sqlite3.Connection, bug: dict[str, Any], shot: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE biz_bug_screenshot SET is_deleted = 1 WHERE id = ? AND bug_id = ?",
        (int(shot["id"]), int(bug["id"])),
    )
    target = file_path(conn, {**shot, "bug_no": bug.get("bug_no")})
    target.unlink(missing_ok=True)
