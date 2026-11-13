"""测试数据样本池与脱敏方案仓储（三期 GAP-39）。"""

from __future__ import annotations

import json
from typing import Any

from ..common.pagination import RowDict
from .base import BaseRepository


def _loads(raw: Any, default: Any) -> Any:
    try:
        value = json.loads(str(raw or ""))
    except (json.JSONDecodeError, TypeError):
        return default
    return value if isinstance(value, type(default)) else default


def scheme_public(row: RowDict) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "name": row.get("name"),
        "description": row.get("description") or "",
        "rules": _loads(row.get("rules_json"), {}),
        "version": int(row.get("version") or 1),
        "isDefault": bool(row.get("is_default")),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def sample_public(row: RowDict) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "name": row.get("name"),
        "sourceFormat": row.get("source_format"),
        "originalFilename": row.get("original_filename") or "",
        "rowCount": int(row.get("row_count") or 0),
        "columnCount": int(row.get("column_count") or 0),
        "columns": _loads(row.get("columns_json"), []),
        "detection": _loads(row.get("detection_json"), {}),
        "rules": _loads(row.get("rules_json"), {}),
        "schemeId": row.get("scheme_id"),
        "schemeVersion": int(row.get("scheme_version") or 1),
        "retentionDays": int(row.get("retention_days") or 30),
        "expiresAt": row.get("expires_at"),
        "status": row.get("status") or "active",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


class MaskSchemeRepository(BaseRepository):
    table = "biz_test_data_mask_scheme"
    default_order = "is_default DESC, id DESC"
    order_whitelist = {"id", "name", "version", "is_default", "created_at", "updated_at"}
    searchable = ["name", "description"]

    def list_for_project(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM biz_test_data_mask_scheme"
            " WHERE is_deleted = 0 AND (project_id = ? OR project_id IS NULL)"
            " ORDER BY is_default DESC, project_id IS NULL, id DESC",
            (project_id,),
        ).fetchall()
        return [scheme_public(dict(row)) for row in rows]

    def get_by_id(self, pk: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_test_data_mask_scheme WHERE id = ? AND is_deleted = 0", (pk,)
        ).fetchone()
        return dict(row) if row else None

    def name_exists(self, project_id: int, name: str, exclude: int | None = None) -> bool:
        row = self.conn.execute(
            "SELECT id FROM biz_test_data_mask_scheme"
            " WHERE is_deleted = 0 AND COALESCE(project_id, 0) = ? AND name = ?"
            " AND id <> ? LIMIT 1",
            (project_id, name, exclude or 0),
        ).fetchone()
        return row is not None

    def clear_defaults(self, project_id: int, keep_id: int) -> None:
        self.conn.execute(
            "UPDATE biz_test_data_mask_scheme SET is_default = 0"
            " WHERE project_id = ? AND is_default = 1 AND id <> ?",
            (project_id, keep_id),
        )

    def referenced_by_samples(self, scheme_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM biz_test_data_sample WHERE scheme_id = ? AND is_deleted = 0",
            (scheme_id,),
        ).fetchone()
        return int(row[0])


class TestDataSampleRepository(BaseRepository):
    table = "biz_test_data_sample"
    default_order = "id DESC"
    order_whitelist = {"id", "name", "row_count", "expires_at", "created_at"}
    searchable = ["name", "original_filename"]

    def get_by_id(self, pk: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_test_data_sample WHERE id = ? AND is_deleted = 0", (pk,)
        ).fetchone()
        return dict(row) if row else None

    def list_for_project(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM biz_test_data_sample WHERE project_id = ? AND is_deleted = 0"
            " ORDER BY id DESC",
            (project_id,),
        ).fetchall()
        return [sample_public(dict(row)) for row in rows]

    def expired(self, now: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM biz_test_data_sample"
            " WHERE is_deleted = 0 AND expires_at <> '' AND expires_at <= ?"
            " ORDER BY id",
            (now,),
        ).fetchall()
        return [dict(row) for row in rows]
