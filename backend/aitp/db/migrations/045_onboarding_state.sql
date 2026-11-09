-- =====================================================================
-- AITP 迁移 045：首启引导状态（GAP-18，二期第 0 批）
--   onboarding.completed 标记「首启引导是否已结束」（完成 / 跳过都算结束）。
--   区分新旧库：库里已有任何项目（含软删）说明是老用户升级，默认 true 不打扰；
--   全新库没有任何项目，默认 false，首启时弹引导。
--   onboarding.reopened_at 记录最近一次从帮助页重开引导的时间，仅作展示，
--   引导是否弹出只看 completed。
-- =====================================================================

INSERT INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
SELECT 'onboarding.completed',
       CASE WHEN (SELECT COUNT(*) FROM biz_project) > 0 THEN 'true' ELSE 'false' END,
       'bool', 'global',
       '首启引导是否已结束（完成或跳过）。true=不再弹出，false=下次进入首页弹出。',
       1, datetime('now'), datetime('now')
WHERE NOT EXISTS (
    SELECT 1 FROM sys_config WHERE config_key = 'onboarding.completed'
);

INSERT INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
SELECT 'onboarding.reopened_at', '', 'string', 'global',
       '最近一次从帮助页重开首启引导的时间（ISO8601），空串表示从未重开。',
       1, datetime('now'), datetime('now')
WHERE NOT EXISTS (
    SELECT 1 FROM sys_config WHERE config_key = 'onboarding.reopened_at'
);
