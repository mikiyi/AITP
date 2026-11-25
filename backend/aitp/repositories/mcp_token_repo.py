"""MCP 访问令牌仓储：只存 SHA-256 哈希与掩码，明文不落库。"""

from __future__ import annotations

from ..common.pagination import RowDict
from .base import BaseRepository

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUS_DELETED = "deleted"


class McpTokenRepository(BaseRepository):
    table = "sys_mcp_token"
    soft_delete_enabled = False
    default_order = "id"
    default_order_dir = "desc"

    def list_recent(self, limit: int = 50) -> list[RowDict]:
        """按创建顺序倒序列出全部令牌（含已停用/已删除的历史记录）。"""
        rows = self.conn.execute(
            f"SELECT * FROM {self.table} ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(row) for row in rows]

    def count_all(self) -> int:
        """令牌总数（含历史记录）：大于 0 表示已启用令牌校验。"""
        row = self.conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()
        return int(row[0])

    def get_active(self) -> RowDict | None:
        row = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE status = ? ORDER BY id DESC LIMIT 1",
            (STATUS_ACTIVE,),
        ).fetchone()
        return dict(row) if row else None

    def verify(self, token_hash: str) -> RowDict | None:
        """按哈希定位生效令牌；停用/删除的历史令牌一律不通过。"""
        row = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE token_hash = ? AND status = ? LIMIT 1",
            (token_hash, STATUS_ACTIVE),
        ).fetchone()
        return dict(row) if row else None

    def touch(self, pk: int, when: str, client: str) -> None:
        """记录最后使用时间与调用客户端（User-Agent 截断到 200 字符）。"""
        self.conn.execute(
            f"UPDATE {self.table} SET last_used_at = ?, last_client = ? WHERE id = ?",
            (when, (client or "")[:200], pk),
        )
