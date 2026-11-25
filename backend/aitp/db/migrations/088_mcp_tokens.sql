-- =====================================================================
-- AITP 迁移 088：MCP 访问令牌改为「哈希入库 + 历史记录」
--   1) 同一时刻只允许一个生效令牌，历史令牌保留状态用于审计；
--   2) 明文不落库，只存 SHA-256 哈希与末四位掩码，明文仅在生成/轮换时返回一次；
--   3) 停用可恢复，删除不可恢复（行保留，状态置 deleted）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS sys_mcp_token (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash     TEXT NOT NULL,
    preview        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    disable_reason TEXT NOT NULL DEFAULT '',
    rotated_from   INTEGER,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    last_used_at   TEXT,
    last_client    TEXT NOT NULL DEFAULT '',
    disabled_at    TEXT,
    deleted_at     TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sys_mcp_token_hash ON sys_mcp_token(token_hash);
CREATE INDEX IF NOT EXISTS idx_sys_mcp_token_status ON sys_mcp_token(status, id);
