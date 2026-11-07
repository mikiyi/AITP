-- =====================================================================
-- AITP 迁移 035：提示词正文自定义
--   磁盘模板（backend/prompts/*.md）是默认值，用户在提示词库改过的正文存这里。
--   一个 code 最多一条：唯一索引 + UPSERT，重置就是删掉这一行回到默认。
--   正文只存覆盖部分（不含 YAML 头），标题/说明/可选变量仍以磁盘文件为准。
-- =====================================================================

CREATE TABLE IF NOT EXISTS sys_ai_prompt_override (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_sys_ai_prompt_override_code
    ON sys_ai_prompt_override(code);
