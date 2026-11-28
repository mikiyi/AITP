"""评测配置仓储：配置行不可变，改了就是新行。"""

from __future__ import annotations

from typing import Any

from .base import BaseRepository


class EvalConfigRepository(BaseRepository):
    table = "biz_eval_config"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "is_baseline", "created_at", "updated_at"}
    searchable = ["name", "provider", "model", "prompt_code", "note"]

    def find_by_name(self, project_id: int, name: str) -> dict[str, Any] | None:
        return self.raw_one(
            "SELECT * FROM biz_eval_config WHERE project_id = ? AND name = ? AND is_deleted = 0",
            (project_id, name),
        )

    def reference_count(self, config_id: int) -> int:
        row = self.raw_one(
            "SELECT COUNT(*) AS total FROM biz_eval_run WHERE config_id = ?",
            (config_id,),
        )
        return int(row["total"]) if row else 0


def config_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "name": row.get("name"),
        "profileId": row.get("profile_id"),
        "provider": row.get("provider"),
        "model": row.get("model"),
        "temperature": row.get("temperature"),
        "topP": row.get("top_p"),
        "maxTokens": row.get("max_tokens"),
        "seed": row.get("seed"),
        "promptCode": row.get("prompt_code"),
        "promptVersion": row.get("prompt_version"),
        "isBaseline": bool(row.get("is_baseline")),
        "note": row.get("note"),
        "referenced": False,
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
