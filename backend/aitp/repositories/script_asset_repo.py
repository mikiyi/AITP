"""P1 #7/#8 资产仓储：脚本版本快照 + UI 元素库（Page Object）。"""

from __future__ import annotations

from .base import BaseRepository
from .numbering import begin_claim


class AutomationScriptVersionRepository(BaseRepository):
    table = "biz_automation_script_version"
    default_order = "version_no"
    default_order_dir = "desc"
    order_whitelist = {"id", "version_no", "created_at"}
    soft_delete_enabled = False

    def next_version_no(self, script_id: int) -> int:
        begin_claim(self.conn)
        row = self.conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) AS m FROM biz_automation_script_version WHERE script_id = ?",
            (script_id,),
        ).fetchone()
        return int(row["m"]) + 1


class UiElementRepository(BaseRepository):
    table = "biz_ui_element"
    default_order = "id"
    default_order_dir = "asc"
    order_whitelist = {"id", "page", "name", "created_at"}
    searchable = ["name", "page", "selector", "description"]