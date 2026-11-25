"""业务对象持久附件：缺陷 / 用例 / 轮次 / 探索会话 / 需求 / 知识条目。

与 ``attachment_service`` 的区别：
- ``attachment_service`` 服务 AI 入口，附件用完即删，只关心文档正文与图片去水印；
- 这里的附件是业务证据与参考资料，必须原样长期保留，文件落
  ``data/business-attachments/<ownerType>/<ownerId>/``，登记表是 ``biz_attachment``。

约束：
- 单文件 ≤ 32MB、单对象 ≤ 50 个；
- 同一对象下 sha256 相同的文件重复上传直接跳过，重试/多端操作不会产生重复附件；
- 只有 PNG/JPEG/GIF/WebP/BMP 允许页面内预览；SVG/HTML 等按普通附件下载，避免脚本注入。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from ..common.secret_store import atomic_write_bytes
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from . import attachment_service

#: 允许挂附件的业务对象：owner_type → 表名（表内必须有 project_id / is_deleted）
OWNER_TABLES: dict[str, str] = {
    "bug": "biz_bug",
    "test_case": "biz_test_case",
    "test_run": "biz_test_run",
    "exploratory_session": "biz_exploration_session",
    "requirement": "biz_requirement",
    "knowledge": "biz_knowledge_article",
}

#: 各归属类型的中文名，用于错误提示
OWNER_LABELS: dict[str, str] = {
    "bug": "缺陷",
    "test_case": "用例",
    "test_run": "测试轮次",
    "exploratory_session": "探索会话",
    "requirement": "需求",
    "knowledge": "知识条目",
}

#: 单文件上限
MAX_BYTES = 32 * 1024 * 1024
#: 单个业务对象最多挂多少个附件
MAX_PER_OWNER = 50

#: 可在页面内直接预览的位图后缀（SVG 不在其中）
PREVIEW_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

STORAGE_DIR = "business-attachments"

_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def _db_dir(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = str((row[2] if row else "") or "")
    return Path(path).parent if path else Path("data")


def root_dir(conn: sqlite3.Connection) -> Path:
    root = _db_dir(conn) / STORAGE_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def owner_dir(conn: sqlite3.Connection, owner_type: str, owner_id: int) -> Path:
    folder = root_dir(conn) / owner_type / str(int(owner_id))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def owner_table(owner_type: str) -> str:
    table = OWNER_TABLES.get(owner_type)
    if not table:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"不支持的附件归属类型：{owner_type or '未填写'}",
        )
    return table


def resolve_owner(
    conn: sqlite3.Connection,
    owner_type: str,
    owner_id: int,
    project_id: int | None,
) -> int | None:
    """确认归属对象存在且属于当前项目，返回它的项目 id。"""
    table = owner_table(owner_type)
    if project_id is None:
        raise AppError(ErrorCode.PARAM_ERROR, "缺少 projectId，无法校验附件归属")
    row = conn.execute(
        f"SELECT project_id FROM {table} WHERE id = ? AND is_deleted = 0",  # noqa: S608
        (int(owner_id),),
    ).fetchone()
    if row is None:
        raise AppError(
            ErrorCode.RESOURCE_NOT_FOUND,
            f"{OWNER_LABELS.get(owner_type, '目标')}不存在",
        )
    owner_project = row["project_id"]
    if owner_project not in (None, "") and int(owner_project) != int(project_id):
        raise AppError(
            ErrorCode.RESOURCE_NOT_FOUND,
            f"{OWNER_LABELS.get(owner_type, '目标')}不属于该项目",
        )
    return int(owner_project) if owner_project not in (None, "") else None


def _safe_suffix(file_name: str) -> str:
    suffix = attachment_service.extension_of(file_name)
    return suffix if _SAFE_SUFFIX.match(suffix) else ""


def _kind_of(file_name: str) -> str:
    return "image" if _safe_suffix(file_name) in PREVIEW_SUFFIXES else "file"


def _public_url(attachment_id: int) -> str:
    return f"/api/v1/attachments/{int(attachment_id)}/file"


def _to_public(row: dict[str, Any]) -> dict[str, Any]:
    attachment_id = int(row.get("id") or 0)
    suffix = _safe_suffix(str(row.get("stored_name") or ""))
    kind = str(row.get("kind") or "file")
    return {
        "id": attachment_id,
        "projectId": row.get("project_id"),
        "ownerType": row.get("owner_type") or "",
        "ownerId": int(row.get("owner_id") or 0),
        "fileName": row.get("original_name") or row.get("file_name") or "",
        "mime": row.get("mime") or "application/octet-stream",
        "sizeBytes": int(row.get("size_bytes") or 0),
        "kind": kind,
        "sha256": row.get("sha256") or "",
        "previewable": kind == "image" and suffix in PREVIEW_SUFFIXES,
        "createdAt": row.get("created_at") or "",
        "url": _public_url(attachment_id),
    }


def list_attachments(
    conn: sqlite3.Connection,
    owner_type: str,
    owner_id: int,
    project_id: int | None,
) -> list[dict[str, Any]]:
    resolve_owner(conn, owner_type, owner_id, project_id)
    rows = conn.execute(
        "SELECT * FROM biz_attachment"
        " WHERE owner_type = ? AND owner_id = ? AND is_deleted = 0"
        " ORDER BY id",
        (owner_type, int(owner_id)),
    ).fetchall()
    return [_to_public(dict(row)) for row in rows]


def get_attachment(
    conn: sqlite3.Connection,
    attachment_id: int,
    project_id: int | None = None,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM biz_attachment WHERE id = ? AND is_deleted = 0",
        (int(attachment_id),),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    owner_project = data.get("project_id")
    if project_id is not None and owner_project not in (None, "") and int(owner_project) != int(project_id):
        return None
    return data


def file_path(conn: sqlite3.Connection, row: dict[str, Any]) -> Path:
    return (
        owner_dir(conn, str(row.get("owner_type") or ""), int(row.get("owner_id") or 0))
        / str(row.get("stored_name") or "")
    )


def save_attachments(
    conn: sqlite3.Connection,
    owner_type: str,
    owner_id: int,
    project_id: int | None,
    files: list[tuple[str, bytes, str]],
) -> dict[str, Any]:
    """批量保存附件；``files`` 为 (原始文件名, 字节, mime)。

    任一文件非法时整体不落盘；同对象下 sha256 重复的返回在 ``skipped`` 里。
    调用方负责 commit。
    """
    owner_project = resolve_owner(conn, owner_type, owner_id, project_id)
    if not files:
        raise AppError(ErrorCode.PARAM_ERROR, "请选择要上传的附件")

    existing = conn.execute(
        "SELECT COUNT(*) FROM biz_attachment"
        " WHERE owner_type = ? AND owner_id = ? AND is_deleted = 0",
        (owner_type, int(owner_id)),
    ).fetchone()[0]
    if int(existing) + len(files) > MAX_PER_OWNER:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"每个{OWNER_LABELS.get(owner_type, '对象')}最多 {MAX_PER_OWNER} 个附件"
            f"（当前已有 {int(existing)} 个）",
        )

    folder = owner_dir(conn, owner_type, int(owner_id))
    now = utcnow_iso()
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    saved_paths: list[Path] = []
    try:
        for original_name, data, mime in files:
            if not data:
                raise AppError(ErrorCode.PARAM_ERROR, f"附件「{original_name}」是空文件")
            if len(data) > MAX_BYTES:
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    f"附件「{original_name}」超过 {MAX_BYTES // (1024 * 1024)}MB 限制",
                )
            digest = hashlib.sha256(data).hexdigest()
            duplicate = conn.execute(
                "SELECT * FROM biz_attachment"
                " WHERE owner_type = ? AND owner_id = ? AND sha256 = ? AND is_deleted = 0"
                " LIMIT 1",
                (owner_type, int(owner_id), digest),
            ).fetchone()
            if duplicate is not None:
                skipped.append(_to_public(dict(duplicate)))
                continue

            display_name = attachment_service.safe_file_name(original_name)
            suffix = _safe_suffix(display_name)
            stored_name = f"{uuid.uuid4().hex}{suffix}"
            target = folder / stored_name
            atomic_write_bytes(target, data, mode=0o600)
            saved_paths.append(target)
            cursor = conn.execute(
                "INSERT INTO biz_attachment"
                " (project_id, owner_type, owner_id, file_name, original_name, mime,"
                "  size_bytes, sha256, kind, stored_name, created_at, is_deleted)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    owner_project,
                    owner_type,
                    int(owner_id),
                    display_name,
                    display_name,
                    mime or "application/octet-stream",
                    len(data),
                    digest,
                    _kind_of(display_name),
                    stored_name,
                    now,
                ),
            )
            created.append({
                "id": int(cursor.lastrowid or 0),
                "projectId": owner_project,
                "ownerType": owner_type,
                "ownerId": int(owner_id),
                "fileName": display_name,
                "mime": mime or "application/octet-stream",
                "sizeBytes": len(data),
                "kind": _kind_of(display_name),
                "sha256": digest,
                "previewable": _kind_of(display_name) == "image",
                "createdAt": now,
                "url": _public_url(int(cursor.lastrowid or 0)),
            })
    except Exception:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise
    return {"list": created, "skipped": skipped}


def delete_attachment(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE biz_attachment SET is_deleted = 1 WHERE id = ?",
        (int(row["id"]),),
    )
    file_path(conn, row).unlink(missing_ok=True)
