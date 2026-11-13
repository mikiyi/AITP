"""外部平台只读导入仓储（三期 GAP-40）。"""

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


def config_public(row: RowDict, *, has_password: bool) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "platform": row.get("platform"),
        "baseUrl": row.get("base_url"),
        "username": row.get("username") or "",
        "productId": row.get("product_id") or "",
        "serverUsername": row.get("server_username") or "",
        "extra": _loads(row.get("extra_json"), {}),
        "hasPassword": has_password,
        "passwordMasked": row.get("auth_mask") or "",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def mapping_public(row: RowDict) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "platform": row.get("platform"),
        "name": row.get("name"),
        "mapping": _loads(row.get("mapping_json"), {}),
        "isDefault": bool(row.get("is_default")),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def batch_public(row: RowDict) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "platform": row.get("platform"),
        "kinds": str(row.get("kinds") or ""),
        "status": row.get("status"),
        "fetched": _loads(row.get("fetched_json"), {}),
        "imported": _loads(row.get("imported_json"), {}),
        "skipped": _loads(row.get("skipped_json"), {}),
        "errors": _loads(row.get("errors_json"), {}),
        "createdAt": row.get("created_at"),
    }


class ExternalImportConfigRepository(BaseRepository):
    table = "biz_external_import_config"
    default_order = "id"
    order_whitelist = {"id", "platform", "created_at", "updated_at"}
    searchable = ["platform", "base_url", "username"]

    def get_by_project_platform(self, project_id: int, platform: str) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_external_import_config"
            " WHERE project_id = ? AND platform = ? AND is_deleted = 0",
            (project_id, platform),
        ).fetchone()
        return dict(row) if row else None

    def upsert(self, project_id: int, platform: str, payload: dict[str, Any]) -> int:
        row = self.get_by_project_platform(project_id, platform)
        if row:
            self.conn.execute(
                "UPDATE biz_external_import_config SET base_url=?, username=?, product_id=?,"
                " server_username=?, extra_json=?, auth_ref=?, auth_mask=?, updated_at=?"
                " WHERE id=?",
                (
                    payload["base_url"], payload["username"], payload["product_id"],
                    payload["server_username"], payload["extra_json"],
                    payload["auth_ref"], payload["auth_mask"], payload["updated_at"],
                    row["id"],
                ),
            )
            return int(row["id"])
        return self.insert(payload)


class ExternalImportMappingRepository(BaseRepository):
    table = "biz_external_import_mapping"
    default_order = "is_default DESC, id"
    order_whitelist = {"id", "name", "platform", "is_default", "created_at", "updated_at"}
    searchable = ["name"]

    def list_for(self, project_id: int, platform: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM biz_external_import_mapping"
            " WHERE project_id = ? AND platform = ? AND is_deleted = 0"
            " ORDER BY is_default DESC, id",
            (project_id, platform),
        ).fetchall()
        return [mapping_public(dict(row)) for row in rows]

    def get_by_id(self, pk: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_external_import_mapping WHERE id = ? AND is_deleted = 0", (pk,)
        ).fetchone()
        return dict(row) if row else None

    def name_exists(self, project_id: int, platform: str, name: str, exclude: int | None = None) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM biz_external_import_mapping"
            " WHERE project_id=? AND platform=? AND name=? AND is_deleted=0 AND id<>? LIMIT 1",
            (project_id, platform, name, exclude or 0),
        ).fetchone() is not None

    def clear_defaults(self, project_id: int, platform: str, keep_id: int) -> None:
        self.conn.execute(
            "UPDATE biz_external_import_mapping SET is_default=0"
            " WHERE project_id=? AND platform=? AND is_default=1 AND id<>?",
            (project_id, platform, keep_id),
        )


class ExternalImportBatchRepository(BaseRepository):
    table = "biz_external_import_batch"
    default_order = "id DESC"
    order_whitelist = {"id", "platform", "status", "created_at"}
    searchable = ["platform"]

    def list_for(self, project_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM biz_external_import_batch"
            " WHERE project_id = ? AND is_deleted = 0 ORDER BY id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [batch_public(dict(row)) for row in rows]
