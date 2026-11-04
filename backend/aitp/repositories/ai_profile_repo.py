"""AI 供应商档案仓储。"""

from __future__ import annotations

from typing import Any

from ..common.env_scope import normalize_scope, read_scoped_env
from ..common.pagination import RowDict, RowList
from ..common.secret_store import get_store
from ..common.security import mask
from .base import BaseRepository, loads


class AIProfileRepository(BaseRepository):
    table = "sys_ai_profile"
    soft_delete_enabled = True
    default_order = "sort_order, id"
    default_order_dir = "asc"
    order_whitelist = {"id", "name", "provider", "sort_order", "priority", "created_at", "updated_at"}
    searchable = ["name", "provider", "model"]

    def get_default(self) -> RowDict | None:
        row = self.conn.execute(
            """
            SELECT * FROM sys_ai_profile
            WHERE is_deleted = 0 AND is_active = 1
            ORDER BY priority DESC, is_default DESC, sort_order ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def get_by_id(self, pk: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM sys_ai_profile WHERE id = ? AND is_deleted = 0", (pk,)
        ).fetchone()
        return dict(row) if row else None

    def clear_other_defaults(self, keep_id: int) -> None:
        """保证「默认档案」全局唯一，否则列表里会出现两个默认标记。"""
        self.conn.execute(
            "UPDATE sys_ai_profile SET is_default = 0 WHERE is_default = 1 AND id <> ?",
            (keep_id,),
        )

    def list_active(self) -> RowList:
        """启用中的档案，按优先级降序（本地模型与云端模型一起排）。"""
        rows = self.conn.execute(
            """
            SELECT * FROM sys_ai_profile
            WHERE is_deleted = 0 AND is_active = 1
            ORDER BY priority DESC, is_default DESC, sort_order ASC, id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- 密钥：本体进 OS 密钥库，库里只留引用与掩码 ----
    @staticmethod
    def key_ref(profile_id: int) -> str:
        return f"ai_profile:{profile_id}"

    def save_api_key(self, profile_id: int, api_key: str) -> str:
        """写入密钥到系统密钥库，返回掩码。数据库不保存明文。"""
        ref = self.key_ref(profile_id)
        store = get_store()
        if api_key:
            store.set(ref, api_key)
        else:
            store.delete(ref)
        masked = mask(api_key) if api_key else ""
        self.update(profile_id, {"api_key_ref": ref, "api_key_masked": masked})
        return masked

    def load_api_key(self, profile_id: int) -> str:
        ref = self.key_ref(profile_id)
        try:
            return get_store().get(ref)
        except Exception:
            return ""

    @staticmethod
    def to_public(row: RowDict) -> dict[str, Any]:
        """对外输出，绝不暴露密文与明文。"""
        extra = loads(row.get("extra_json"), {}) or {}
        use_env_key = bool(row.get("use_env_key"))
        env_key_name = str(row.get("env_key_name") or "")
        env_key_scope = normalize_scope(row.get("env_key_scope"))
        env_key_present = bool(
            use_env_key and env_key_name and read_scoped_env(env_key_name, env_key_scope)
        )
        return {
            "id": row.get("id"),
            "name": row.get("name"),
            "provider": row.get("provider"),
            "protocol": row.get("protocol"),
            "baseUrl": row.get("base_url"),
            "model": row.get("model"),
            "apiKeyMasked": row.get("api_key_masked") or "",
            "hasApiKey": bool(row.get("api_key_ref") or row.get("api_key_enc")) or env_key_present,
            "useEnvKey": use_env_key,
            "envKeyName": env_key_name,
            "envKeyScope": env_key_scope,
            "envKeyPresent": env_key_present,
            "extra": extra,
            "temperature": row.get("temperature"),
            "maxTokens": row.get("max_tokens"),
            "timeout": row.get("timeout"),
            "isDefault": bool(row.get("is_default")),
            "isActive": bool(row.get("is_active")),
            "sortOrder": row.get("sort_order"),
            "priority": row.get("priority") or 0,
            "isLocal": bool(row.get("is_local")),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
        }
