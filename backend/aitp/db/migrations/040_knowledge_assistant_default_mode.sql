-- =====================================================================
-- AITP 迁移 040：知识库问答助手默认回答模式
--   问答助手打开时预选哪种模式，由「设置 → 知识库设置」维护。
--   默认 hybrid（知识库+大模型补强），与历史行为保持一致。
-- =====================================================================

INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('knowledge.assistant_default_mode', 'hybrid', 'string', 'global',
     '知识库问答助手默认回答模式：kb=基于知识库 / llm=基于大模型 / hybrid=知识库+大模型补强', 1,
     datetime('now'), datetime('now'));
