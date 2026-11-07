-- GAP-11: AI 外发脱敏结果落库（只存类型与数量，不存原文）
ALTER TABLE biz_ai_record ADD COLUMN mask_json TEXT NOT NULL DEFAULT '{}';

INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('ai.mask.enabled', 'true', 'bool', 'global',
     '调用云端模型前是否自动脱敏手机号、身份证、邮箱、API Key 等敏感信息',
     1, datetime('now'), datetime('now'));
