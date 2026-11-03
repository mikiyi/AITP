-- =====================================================================
-- AITP 迁移 004：审计日志 + AI 模型优先级 + 降级策略配置
-- =====================================================================

-- ---------------------------------------------------------------------
-- 关键写操作审计：谁、做了什么、改了哪条记录、请求来自哪
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sys_audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    actor          TEXT NOT NULL DEFAULT 'local',
    action         TEXT NOT NULL,
    resource_type  TEXT NOT NULL,
    resource_id    INTEGER,
    resource_label TEXT,
    detail_json    TEXT,
    result         TEXT NOT NULL DEFAULT 'success',
    trace_id       TEXT,
    client_ip      TEXT,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sys_audit_log_action   ON sys_audit_log(action, created_at);
CREATE INDEX IF NOT EXISTS idx_sys_audit_log_resource ON sys_audit_log(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_sys_audit_log_created  ON sys_audit_log(created_at);

-- ---------------------------------------------------------------------
-- AI 档案：优先级（数据越大越优先）+ 本地模型标记 + 数据外发属性
-- ---------------------------------------------------------------------
ALTER TABLE sys_ai_profile ADD COLUMN priority  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sys_ai_profile ADD COLUMN is_local  INTEGER NOT NULL DEFAULT 0;
-- 密钥引用：本体存 OS 密钥库（DPAPI / 钥匙串），库里只留引用与掩码
ALTER TABLE sys_ai_profile ADD COLUMN api_key_ref TEXT;

CREATE INDEX IF NOT EXISTS idx_sys_ai_profile_priority ON sys_ai_profile(is_active, priority);

-- ---------------------------------------------------------------------
-- 降级策略配置
-- ---------------------------------------------------------------------
INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('ai.degrade.enabled', 'true', 'bool', 'global', 'AI 不可用时是否按优先级自动降级到下一个模型', 1,
     datetime('now'), datetime('now')),
    ('ai.timeout.default', '60', 'int', 'global', 'AI 调用默认超时秒数', 1,
     datetime('now'), datetime('now'));
