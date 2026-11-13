"""测试数据样本池与脱敏方案服务（三期 GAP-39）。

原始样本只在导入请求的内存里解析；落盘的是脱敏后的 JSON 副本，
库内只存列结构、命中类型、规则快照、方案版本与保留期。
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import current_app

from ..common.secret_store import atomic_write_bytes
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.test_data_sample_repo import (
    MaskSchemeRepository,
    TestDataSampleRepository,
    sample_public,
    scheme_public,
)
from . import test_data_mask

MAX_SCHEME_NAME = 100
MAX_SAMPLE_NAME = 100
DEFAULT_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 3650


def samples_root(db_path: Path | None = None) -> Path:
    if db_path is None:
        db_path = Path(current_app.config["AITP_DB_PATH"])
    root = db_path.parent / "samples"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _clean_name(raw: Any, field: str, limit: int) -> str:
    name = str(raw or "").strip()
    if not name:
        raise AppError(ErrorCode.PARAM_ERROR, f"{field}不能为空")
    if len(name) > limit:
        raise AppError(ErrorCode.PARAM_ERROR, f"{field}不能超过 {limit} 个字符")
    return name


def _retention(raw: Any) -> int:
    if raw in (None, ""):
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, "保留天数必须是整数") from None
    if days < 1 or days > MAX_RETENTION_DAYS:
        raise AppError(ErrorCode.PARAM_ERROR, f"保留天数必须在 1~{MAX_RETENTION_DAYS} 之间")
    return days


def _expires_at(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sample_dir(root: Path, sample_id: int) -> Path:
    path = (root / str(sample_id)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AppError(ErrorCode.PARAM_ERROR, "样本目录非法")
    return path


# ---------------- 脱敏方案 ----------------

def list_schemes(conn: Any, project_id: int) -> list[dict[str, Any]]:
    return MaskSchemeRepository(conn).list_for_project(project_id)


def create_scheme(
    conn: Any, *, project_id: int, name: str, description: str = "",
    rules: Any = None, is_default: bool = False,
) -> dict[str, Any]:
    repo = MaskSchemeRepository(conn)
    clean = _clean_name(name, "方案名称", MAX_SCHEME_NAME)
    if repo.name_exists(project_id, clean):
        raise AppError(ErrorCode.PARAM_ERROR, f"脱敏方案「{clean}」已存在")
    now = utcnow_iso()
    new_id = repo.insert({
        "project_id": project_id,
        "name": clean,
        "description": str(description or "")[:500],
        "rules_json": json.dumps(test_data_mask.normalize_rules(rules), ensure_ascii=False),
        "version": 1,
        "is_default": 1 if is_default else 0,
        "created_at": now,
        "updated_at": now,
    })
    if is_default:
        repo.clear_defaults(project_id, new_id)
    return scheme_public(repo.get_by_id(new_id) or {})


def update_scheme(
    conn: Any, scheme_id: int, *, project_id: int, name: Any = None,
    description: Any = None, rules: Any = None, is_default: Any = None,
) -> dict[str, Any]:
    repo = MaskSchemeRepository(conn)
    row = repo.get_by_id(scheme_id)
    if not row or int(row.get("project_id") or 0) != project_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脱敏方案不存在")
    payload: dict[str, Any] = {"updated_at": utcnow_iso()}
    if name is not None:
        clean = _clean_name(name, "方案名称", MAX_SCHEME_NAME)
        if repo.name_exists(project_id, clean, exclude=scheme_id):
            raise AppError(ErrorCode.PARAM_ERROR, f"脱敏方案「{clean}」已存在")
        payload["name"] = clean
    if description is not None:
        payload["description"] = str(description)[:500]
    if rules is not None:
        payload["rules_json"] = json.dumps(
            test_data_mask.normalize_rules(rules), ensure_ascii=False)
        payload["version"] = int(row.get("version") or 1) + 1
    if is_default is not None:
        payload["is_default"] = 1 if is_default else 0
    repo.update(scheme_id, payload)
    if is_default:
        repo.clear_defaults(project_id, scheme_id)
    return scheme_public(repo.get_by_id(scheme_id) or {})


def delete_scheme(conn: Any, scheme_id: int, *, project_id: int) -> None:
    repo = MaskSchemeRepository(conn)
    row = repo.get_by_id(scheme_id)
    if not row or int(row.get("project_id") or 0) != project_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脱敏方案不存在")
    count = repo.referenced_by_samples(scheme_id)
    if count:
        raise AppError(
            ErrorCode.RESOURCE_REFERENCED,
            f"该方案仍被 {count} 个样本引用，请先删除样本或改用其他方案",
        )
    repo.soft_delete(scheme_id)


# ---------------- 样本 ----------------

def import_sample(
    conn: Any, *, project_id: int, name: str, filename: str, data: bytes,
    scheme_id: int | None = None, retention_days: Any = None, db_path: Path | None = None,
) -> dict[str, Any]:
    clean_name = _clean_name(name, "样本名称", MAX_SAMPLE_NAME)
    source_format, columns, rows = test_data_mask.parse_sample(data, filename)
    detected = test_data_mask.detect_columns(columns, rows)

    scheme_row: dict[str, Any] | None = None
    if scheme_id is not None:
        scheme_row = MaskSchemeRepository(conn).get_by_id(int(scheme_id))
        if not scheme_row:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脱敏方案不存在")
        owner = scheme_row.get("project_id")
        if owner is not None and int(owner) != project_id:
            raise AppError(ErrorCode.PARAM_ERROR, "脱敏方案不属于当前项目")
        raw_rules = json.loads(str(scheme_row.get("rules_json") or "{}"))
        rules = test_data_mask.normalize_rules(raw_rules, columns)
    else:
        rules = test_data_mask.auto_rules(detected)

    missing = [column for column in detected if column not in rules]
    if missing:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"以下敏感列还没有脱敏规则：{'、'.join(missing)}；请先补规则再导入",
        )
    masked_rows, counts = test_data_mask.apply_rules(rows, rules)
    leaks = test_data_mask.validate_masked(masked_rows, columns)
    if leaks:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"脱敏后仍检测到敏感信息：{'、'.join(sorted(set(leaks)))}；已拒绝落盘",
        )

    retention = _retention(retention_days)
    repo = TestDataSampleRepository(conn)
    now = utcnow_iso()
    new_id = repo.insert({
        "project_id": project_id,
        "name": clean_name,
        "source_format": source_format,
        "original_filename": Path(filename or "").name[:200],
        "row_count": len(masked_rows),
        "column_count": len(columns),
        "columns_json": json.dumps(columns, ensure_ascii=False),
        "scheme_id": int(scheme_id) if scheme_id is not None else None,
        "scheme_version": int((scheme_row or {}).get("version") or 1),
        "detection_json": json.dumps(detected, ensure_ascii=False),
        "rules_json": json.dumps(rules, ensure_ascii=False),
        "rel_path": "",
        "retention_days": retention,
        "expires_at": _expires_at(retention),
        "status": "active",
        "created_at": now,
        "updated_at": now,
    })
    root = samples_root(db_path)
    directory = _sample_dir(root, new_id)
    try:
        payload = json.dumps(masked_rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        atomic_write_bytes(directory / "masked.json", payload)
        rel_path = f"samples/{new_id}/masked.json"
        repo.update(new_id, {"rel_path": rel_path, "updated_at": utcnow_iso()})
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        repo.soft_delete(new_id)
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            f"脱敏副本写入失败：{exc.strerror or exc}",
        ) from None
    result = sample_public(repo.get_by_id(new_id) or {})
    result["maskedCounts"] = counts
    return result


def list_samples(conn: Any, project_id: int) -> list[dict[str, Any]]:
    cleanup_expired(conn)
    return TestDataSampleRepository(conn).list_for_project(project_id)


def get_sample(conn: Any, sample_id: int, *, project_id: int) -> dict[str, Any]:
    row = TestDataSampleRepository(conn).get_by_id(sample_id)
    if not row or int(row.get("project_id") or 0) != project_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "样本不存在")
    return sample_public(row)


def delete_sample(conn: Any, sample_id: int, *, project_id: int, db_path: Path | None = None) -> None:
    repo = TestDataSampleRepository(conn)
    row = repo.get_by_id(sample_id)
    if not row or int(row.get("project_id") or 0) != project_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "样本不存在")
    shutil.rmtree(_sample_dir(samples_root(db_path), sample_id), ignore_errors=True)
    repo.soft_delete(sample_id)


def read_sample(
    conn: Any, sample_id: int, *, project_id: int, fmt: str = "json",
    db_path: Path | None = None,
) -> tuple[bytes, str, str]:
    row = TestDataSampleRepository(conn).get_by_id(sample_id)
    if not row or int(row.get("project_id") or 0) != project_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "样本不存在")
    path = samples_root(db_path) / str(sample_id) / "masked.json"
    if not path.is_file():
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脱敏副本不存在，可能已被清理")
    rows = json.loads(path.read_text(encoding="utf-8"))
    name = str(row.get("name") or f"sample-{sample_id}").replace("/", "_").replace("\\", "_")
    if fmt == "csv":
        columns = json.loads(str(row.get("columns_json") or "[]"))
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        for item in rows:
            writer.writerow([item.get(column, "") for column in columns])
        return buffer.getvalue().encode("utf-8-sig"), f"{name}.csv", "text/csv"
    return (
        json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"),
        f"{name}.json",
        "application/json",
    )


def cleanup_expired(conn: Any, db_path: Path | None = None) -> list[dict[str, Any]]:
    """到期样本：删脱敏副本、软删记录，返回被清理的样本摘要。"""
    repo = TestDataSampleRepository(conn)
    removed: list[dict[str, Any]] = []
    root = samples_root(db_path)
    for row in repo.expired(utcnow_iso()):
        sample_id = int(row["id"])
        shutil.rmtree(_sample_dir(root, sample_id), ignore_errors=True)
        conn.execute(
            "UPDATE biz_test_data_sample SET status = 'expired', is_deleted = 1, updated_at = ?"
            " WHERE id = ?",
            (utcnow_iso(), sample_id),
        )
        removed.append({"id": sample_id, "name": str(row.get("name") or "")})
    return removed


def cleanup_preview(conn: Any) -> list[dict[str, Any]]:
    return [
        {"id": int(row["id"]), "name": str(row.get("name") or ""), "expiresAt": row.get("expires_at")}
        for row in TestDataSampleRepository(conn).expired(utcnow_iso())
    ]
