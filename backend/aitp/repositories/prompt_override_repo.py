"""提示词正文自定义仓储。

磁盘模板是默认值，这里只存「用户改过的正文」：一个 code 一行，重置即删行。
正文不含 YAML 头，标题、说明、可选变量仍然只认磁盘文件，避免两处各说一套。
"""

from __future__ import annotations

from ..common.time_utils import utcnow_iso
from .base import BaseRepository

#: 自定义正文长度上限，够写完整提示词又不至于把配置表撑爆
MAX_BODY_LENGTH = 20000


class PromptOverrideRepository(BaseRepository):
    table = "sys_ai_prompt_override"
    soft_delete_enabled = False

    def get_body(self, code: str) -> str | None:
        row = self.conn.execute(
            "SELECT body FROM sys_ai_prompt_override WHERE code = ?", (code,)
        ).fetchone()
        return str(row["body"]) if row else None

    def list_bodies(self) -> dict[str, str]:
        """全部自定义正文，供注册表一次性装载。"""
        rows = self.conn.execute("SELECT code, body FROM sys_ai_prompt_override").fetchall()
        return {str(r["code"]): str(r["body"]) for r in rows}

    def save(self, code: str, body: str) -> None:
        now = utcnow_iso()
        self.conn.execute(
            """
            INSERT INTO sys_ai_prompt_override (code, body, created_at, updated_at, is_deleted)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(code) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at
            """,
            (code, body, now, now),
        )

    def remove(self, code: str) -> bool:
        cursor = self.conn.execute("DELETE FROM sys_ai_prompt_override WHERE code = ?", (code,))
        return cursor.rowcount > 0
