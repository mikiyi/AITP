-- =====================================================================
-- AITP 迁移 033：定时整包备份与保留策略
--   设置项存在 sys_config，重启后计划不丢；next_run_at 由服务按计划推进，
--   服务停机错过的时间点在下次启动时补跑一次。
-- =====================================================================

INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('backup.auto.enabled', 'true', 'bool', 'global',
     '是否按计划自动生成整包备份', 1, datetime('now'), datetime('now')),
    ('backup.auto.frequency', 'daily', 'string', 'global',
     '自动备份频率：daily 每天 / weekly 每周', 1, datetime('now'), datetime('now')),
    ('backup.auto.time', '02:00', 'string', 'global',
     '自动备份执行时间（本机时间，HH:MM）', 1, datetime('now'), datetime('now')),
    ('backup.auto.weekday', '1', 'int', 'global',
     '每周备份的星期几：1=周一 … 7=周日', 1, datetime('now'), datetime('now')),
    ('backup.auto.retention', '7', 'int', 'global',
     '自动备份保留份数，超出后删除最旧的自动备份（不动手动与恢复前备份）', 1,
     datetime('now'), datetime('now')),
    ('backup.auto.min_free_mb', '512', 'int', 'global',
     '磁盘剩余空间低于该值时跳过本次自动备份并告警', 1, datetime('now'), datetime('now')),
    ('backup.auto.next_run_at', '', 'string', 'global',
     '下次自动备份时间（服务维护，无需手工填写）', 1, datetime('now'), datetime('now')),
    ('backup.auto.last_run_at', '', 'string', 'global',
     '最近一次自动备份时间（服务维护）', 1, datetime('now'), datetime('now')),
    ('backup.auto.last_status', '', 'string', 'global',
     '最近一次自动备份结果：success / skipped / failed / running', 1,
     datetime('now'), datetime('now')),
    ('backup.auto.last_message', '', 'string', 'global',
     '最近一次自动备份结果说明（服务维护）', 1, datetime('now'), datetime('now'));
