"""系统配置仓储：sys_config 是 KV 表，没有自增主键。"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..common.time_utils import utcnow_iso


class SysConfigRepository:
    table = "sys_config"

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM sys_config WHERE config_key = ?", (key,)
        ).fetchone()
        return dict(row) if row else None

    def get_value(self, key: str, default: Any = None) -> Any:
        row = self.get(key)
        if not row:
            return default
        return row.get("config_value")

    def list_all(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM sys_config ORDER BY config_key ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_value(self, key: str, value: str, value_type: str = "string",
                  description: str = "", scope: str = "global",
                  is_system: int = 0) -> None:
        now = utcnow_iso()
        self.conn.execute(
            """
            INSERT INTO sys_config
                (config_key, config_value, value_type, scope, description, is_system,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(config_key) DO UPDATE SET
                config_value = excluded.config_value,
                value_type = excluded.value_type,
                scope = excluded.scope,
                description = excluded.description,
                updated_at = excluded.updated_at
            """,
            (key, value, value_type, scope, description, is_system, now, now),
        )

    def delete(self, key: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM sys_config WHERE config_key = ? AND is_system = 0", (key,)
        )
        return cursor.rowcount > 0
