-- =====================================================================
-- AITP 迁移 086：知识库问答历史对话归档
--   知识库问答会话超过 50 轮（100 条消息）时，最早的对话自动归档，
--   页面只展示最近 50 轮；归档以每 50 轮一组落库，
--   命名格式为「历史对话归档 YYYYMMDDHHMM」。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_assistant_archive (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL,
    archive_no    TEXT NOT NULL,                  -- YYYYMMDDHHMM，如 202611241518
    archive_name  TEXT NOT NULL,                  -- 历史对话归档 YYYYMMDDHHMM
    turn_count    INTEGER NOT NULL DEFAULT 0,     -- 归档包含的轮数（一问一答=1轮）
    message_count INTEGER NOT NULL DEFAULT 0,     -- 归档包含的消息条数
    period_from   TEXT NOT NULL DEFAULT '',       -- 归档对话的起始时间
    period_to     TEXT NOT NULL DEFAULT '',       -- 归档对话的结束时间
    content_json  TEXT NOT NULL DEFAULT '[]',     -- 归档的完整对话内容
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_assistant_archive_no
    ON biz_assistant_archive(archive_no, session_id) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_assistant_archive_session
    ON biz_assistant_archive(session_id, created_at) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_assistant_archive_created
    ON biz_assistant_archive(created_at) WHERE is_deleted = 0;
