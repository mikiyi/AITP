-- =====================================================================
-- AITP 迁移 055：数据留存与归档（GAP-44）
--   biz_archive_bundle  一次归档动作的包裹：类型、条数、压缩包位置
--   biz_archive_item    归档条目索引：不解压就能检索，回看再按 ref_id 解包
--   sys_config          各数据类型的保留天数与到期动作（archive/delete/summary）
--   归档只落本机 data/archive/，清理一律软删（审计日志无软删列，归档后物理删）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_archive_bundle (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_no   TEXT NOT NULL,
    data_type   TEXT NOT NULL,                -- test_run / automation_run / evidence / log / todo
    action      TEXT NOT NULL DEFAULT 'archive',
    item_count  INTEGER NOT NULL DEFAULT 0,
    period_from TEXT NOT NULL DEFAULT '',
    period_to   TEXT NOT NULL DEFAULT '',
    file_path   TEXT NOT NULL DEFAULT '',     -- 相对 data 目录：archive/<type>/<no>.zip
    file_size   INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_archive_bundle_no
    ON biz_archive_bundle(bundle_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_archive_bundle_type
    ON biz_archive_bundle(data_type, created_at) WHERE is_deleted = 0;

CREATE TABLE IF NOT EXISTS biz_archive_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id   INTEGER NOT NULL REFERENCES biz_archive_bundle(id),
    data_type   TEXT NOT NULL,
    ref_id      INTEGER NOT NULL DEFAULT 0,   -- 原表行 id；回看原文时按它解包定位
    title       TEXT NOT NULL DEFAULT '',
    happened_at TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_archive_item_bundle
    ON biz_archive_item(bundle_id) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_archive_item_type
    ON biz_archive_item(data_type, ref_id) WHERE is_deleted = 0;

-- ---------------------------------------------------------------------
-- 留存策略默认配置：days=0 表示永久保留；action ∈ archive/delete/summary
-- ---------------------------------------------------------------------
INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('retention.test_run.days', '365', 'int', 'global',
     '测试轮次保留天数；0=永久。到期动作见 retention.test_run.action', 1,
     datetime('now'), datetime('now')),
    ('retention.test_run.action', 'archive', 'string', 'global',
     '测试轮次到期动作：archive=归档后软删 / delete=软删 / summary=仅留摘要（留轮次头、清用例快照）', 1,
     datetime('now'), datetime('now')),
    ('retention.automation_run.days', '180', 'int', 'global',
     '自动化执行记录保留天数；0=永久。执行矩阵引用的记录到期不清', 1,
     datetime('now'), datetime('now')),
    ('retention.automation_run.action', 'archive', 'string', 'global',
     '自动化执行记录到期动作：archive / delete / summary（清 stdout/stderr）', 1,
     datetime('now'), datetime('now')),
    ('retention.evidence.days', '180', 'int', 'global',
     '执行证据保留天数；0=永久。缺陷引用的证据到期不清', 1,
     datetime('now'), datetime('now')),
    ('retention.evidence.action', 'archive', 'string', 'global',
     '执行证据到期动作：archive / delete / summary（清请求/响应/日志正文）', 1,
     datetime('now'), datetime('now')),
    ('retention.log.days', '180', 'int', 'global',
     '操作日志保留天数；0=永久。日志表无软删列，archive=归档后物理删', 1,
     datetime('now'), datetime('now')),
    ('retention.log.action', 'archive', 'string', 'global',
     '操作日志到期动作：archive / delete', 1,
     datetime('now'), datetime('now')),
    ('retention.todo.days', '180', 'int', 'global',
     '已完成待办保留天数；0=永久', 1,
     datetime('now'), datetime('now')),
    ('retention.todo.action', 'archive', 'string', 'global',
     '已完成待办到期动作：archive / delete', 1,
     datetime('now'), datetime('now')),
    ('retention.cleanup.auto', '1', 'bool', 'global',
     '启动时自动补一次到期清理（距上次超过 20 小时才补）；0=只手动执行', 1,
     datetime('now'), datetime('now')),
    ('retention.cleanup.last_run', '', 'string', 'global',
     '上次自动清理时间（ISO），由清理任务回写', 1,
     datetime('now'), datetime('now'));
