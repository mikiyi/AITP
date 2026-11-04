"""测试数据生成服务（文件分支）。

一次调用 = 一个批次：同一种格式、同一套命名规则、N 个文件。
产物落在 data/generated/<批次号>/ 下，库里只记相对路径与实际字节数，
删除批次时连目录一起清掉，避免本机会攒下一堆没人要的测试文件。

命名规则：``[前缀_][主体][_后缀][_标记]``，标记按 token_mode 取自增序号 / 时间戳。
主体默认 ``aitp``，让文件名本身就说明来源。
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import current_app

from ..common.pagination import Page
from ..common.secret_store import atomic_write_bytes
from ..errors import AppError, ErrorCode
from ..repositories.test_data_repo import (
    TOKEN_MODES,
    TestDataBatchRepository,
    TestDataFileRepository,
    decorate_batch,
    decorate_file,
)
from .test_data_builder import MAX_BUILD_BYTES, BuildError, build_file
from .test_data_formats import FORMAT_BY_EXT, FileFormat, resolve_format

#: 单批次文件数量上限：再多就该分批次，方便按批次清理
MAX_FILES_PER_BATCH = 200
#: 单批次总字节上限，防止一次点错把磁盘写满
MAX_BATCH_BYTES = 1024 * 1024 * 1024
#: 前缀 / 后缀允许的字符：字母数字和下划线连字符，杜绝路径分隔符
_NAME_PART_RE = re.compile(r"[A-Za-z0-9_-]{0,40}")

TOKEN_TIMESTAMP = "%Y%m%d%H%M%S"


def generated_root(db_path: Path | None = None) -> Path:
    """生成文件根目录：与数据库同级，随 data 目录一起备份或清理。"""
    if db_path is None:
        db_path = Path(current_app.config["AITP_DB_PATH"])
    root = db_path.parent / "generated"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _normalize(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not _NAME_PART_RE.fullmatch(text):
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{field}只能使用字母、数字、下划线和连字符，且不超过 40 个字符",
        )
    return text


def _resolve_target(ext: Any) -> FileFormat:
    try:
        return resolve_format(str(ext or ""))
    except KeyError:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"不支持的文件格式「{ext}」，请从格式目录里选择",
        ) from None


def _token(index: int, total: int, token_mode: str, stamp: str) -> str:
    if token_mode == "sequence":
        return f"{index:0{max(2, len(str(total)))}d}"
    if token_mode == "timestamp":
        return stamp
    if token_mode == "timestamp_sequence":
        return f"{stamp}{index:0{max(2, len(str(total)))}d}"
    return ""


def _file_name(fmt: FileFormat, index: int, total: int, token_mode: str,
               prefix: str, suffix: str, stamp: str) -> str:
    """同一个批次里若不允许编号，重复的名字会被覆盖，因此强制补序号。"""
    token = _token(index, total, token_mode, stamp)
    if not token and total > 1:
        token = f"{index:02d}"
    parts = [part for part in (prefix, "aitp", suffix, token) if part]
    return f"{'_'.join(parts)}.{fmt.ext}"


def _safe_batch_dir(root: Path, dir_name: str) -> Path:
    """把批次目录限制在 generated 根目录内，禁止 .. 越界。"""
    path = (root / dir_name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AppError(ErrorCode.PARAM_ERROR, "批次目录非法")
    return path


def generate_files(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    module_id: int | None,
    ext: Any,
    size_bytes: Any = None,
    file_count: Any = 1,
    name_prefix: Any = "",
    name_suffix: Any = "",
    token_mode: Any = "none",
    remark: Any = "",
) -> dict[str, Any]:
    """按格式与命名规则批量生成文件并落盘、入库。"""
    fmt = _resolve_target(ext)
    prefix = _normalize(name_prefix, "文件名前缀")
    suffix = _normalize(name_suffix, "文件名后缀")
    token = str(token_mode or "none").strip().lower()
    if token not in TOKEN_MODES:
        raise AppError(ErrorCode.PARAM_ERROR, "文件名标记只能是 none / sequence / timestamp / 组合")

    size = None
    # 只有「没填」才走最小规格；填了 0 或负数是非法输入，必须报错而不是悄悄换成默认值
    if size_bytes is not None and str(size_bytes).strip() != "":
        try:
            size = int(size_bytes)
        except (TypeError, ValueError):
            raise AppError(ErrorCode.PARAM_ERROR, "文件大小必须是整数字节") from None
        if size <= 0:
            raise AppError(ErrorCode.PARAM_ERROR, "文件大小必须大于 0 字节")
        if size > MAX_BUILD_BYTES:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"单个文件不能超过 {MAX_BUILD_BYTES // 1024 // 1024} MB",
            )

    try:
        count = int(file_count) if file_count not in (None, "") else 1
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, "文件数量必须是整数") from None
    if count <= 0:
        raise AppError(ErrorCode.PARAM_ERROR, "文件数量必须大于 0")
    if count > MAX_FILES_PER_BATCH:
        raise AppError(ErrorCode.PARAM_ERROR, f"单批次最多生成 {MAX_FILES_PER_BATCH} 个文件")
    if size is not None and size * count > MAX_BATCH_BYTES:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"单批次总大小不能超过 {MAX_BATCH_BYTES // 1024 // 1024 // 1024} GB，请减少数量或大小",
        )

    repo = TestDataBatchRepository(conn)
    file_repo = TestDataFileRepository(conn)
    batch_no = repo.next_batch_no(project_id)
    root = generated_root()
    batch_dir = _safe_batch_dir(root, batch_no)
    batch_dir.mkdir(parents=True, exist_ok=True)

    batch_id = repo.insert({
        "project_id": project_id,
        "module_id": module_id,
        "batch_no": batch_no,
        "ext": fmt.ext,
        "format_label": fmt.label,
        "build_mode": fmt.mode,
        "size_bytes": size,
        "file_count": count,
        "created_files": 0,
        "total_bytes": 0,
        "name_prefix": prefix,
        "name_suffix": suffix,
        "token_mode": token,
        "dir_name": batch_no,
        "remark": str(remark or "")[:200],
    })

    stamp = datetime.now().strftime(TOKEN_TIMESTAMP)
    created: list[dict[str, Any]] = []
    total_bytes = 0
    try:
        for index in range(1, count + 1):
            name = _file_name(fmt, index, count, token, prefix, suffix, stamp)
            try:
                built = build_file(fmt, size)
            except BuildError as exc:
                raise AppError(ErrorCode.PARAM_ERROR, str(exc)) from None
            atomic_write_bytes(batch_dir / name, built.data, mode=0o600)
            total_bytes += built.actual
            if total_bytes > MAX_BATCH_BYTES:
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    f"批次累计大小超过 {MAX_BATCH_BYTES // 1024 // 1024 // 1024} GB，已中断",
                )
            file_id = file_repo.insert({
                "batch_id": batch_id,
                "file_name": name,
                "ext": fmt.ext,
                "size_bytes": built.actual,
                "exact": 1 if built.exact else 0,
                "note": built.note,
                "rel_path": f"{batch_no}/{name}",
            })
            created.append({
                "id": file_id,
                "batchId": batch_id,
                "fileName": name,
                "ext": fmt.ext,
                "sizeBytes": built.actual,
                "exact": built.exact,
                "note": built.note,
                "relPath": f"{batch_no}/{name}",
            })
    except BaseException:
        # 中途失败不留下半截批次：文件、明细行和批次一起回滚
        shutil.rmtree(batch_dir, ignore_errors=True)
        file_repo.delete_by_batch(batch_id)
        repo.delete(batch_id)
        raise

    repo.update(batch_id, {"created_files": len(created), "total_bytes": total_bytes})
    conn.commit()
    batch = decorate_batch(repo.get(batch_id) or {})
    batch["files"] = created
    return batch


def list_batches(conn: sqlite3.Connection, project_id: int | None, page: int, size: int,
                 keyword: str | None) -> Page:
    """批次列表只带统计，明细按需展开，避免一次拉出上千个文件名。"""
    repo = TestDataBatchRepository(conn)
    result = repo.list(
        filters={"project_id": project_id} if project_id else None,
        keyword=keyword,
        page_num=page,
        page_size=size,
    )
    result.list = [decorate_batch(row) for row in result.list]
    return result


def files_of(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    return [decorate_file(row) for row in TestDataFileRepository(conn).list_by_batch(batch_id)]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = TestDataBatchRepository(conn).get(batch_id)
    if not row:
        return None
    batch = decorate_batch(row)
    batch["files"] = files_of(conn, batch_id)
    return batch


def _locate(conn: sqlite3.Connection, file_id: int) -> tuple[Path, str]:
    """取文件绝对路径，并挡住越界读取。"""
    file_repo = TestDataFileRepository(conn)
    row = file_repo.get(file_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文件不存在或已删除")
    batch = TestDataBatchRepository(conn).get(int(row["batch_id"]))
    if not batch:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "批次不存在或已删除")
    root = generated_root().resolve()
    path = (root / str(row["rel_path"])).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文件已被清理，请重新生成")
    return path, str(row["file_name"])


def read_file(conn: sqlite3.Connection, file_id: int) -> tuple[bytes, str, str]:
    path, name = _locate(conn, file_id)
    fmt = FORMAT_BY_EXT.get(path.suffix.lstrip(".").lower())
    media_type = fmt.media_type if fmt else "application/octet-stream"
    return path.read_bytes(), name, media_type


def read_archive(conn: sqlite3.Connection, batch_id: int) -> tuple[bytes, str]:
    """把整个批次打成一个 zip：测试数据往往一次就要全部拿走。"""
    batch = get_batch(conn, batch_id)
    if not batch:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "批次不存在或已删除")
    root = generated_root().resolve()
    buffer = BytesIO()
    written = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in batch["files"]:
            path = (root / str(item["relPath"])).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            archive.write(path, arcname=str(item["fileName"]))
            written += 1
    if not written:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "批次里的文件已被清理，请重新生成")
    return buffer.getvalue(), f"{batch['batchNo']}.zip"


def delete_batch(conn: sqlite3.Connection, batch_id: int) -> bool:
    """软删记录 + 删文件：测试数据不留残骸。"""
    repo = TestDataBatchRepository(conn)
    row = repo.get(batch_id)
    if not row:
        return False
    root = generated_root().resolve()
    dir_name = str(row.get("dir_name") or row.get("batch_no") or "")
    if dir_name:
        path = (root / dir_name).resolve()
        if path.is_relative_to(root) and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    repo.soft_delete(batch_id)
    TestDataFileRepository(conn).delete_by_batch(batch_id)
    conn.commit()
    return True
