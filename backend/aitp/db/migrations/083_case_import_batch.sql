-- =====================================================================
-- AITP 迁移 083：历史资产导入批次（V2.6.0）
--   支持 xlsx / csv / json 三种存量用例导入，先干跑预览再落库；
--   每条导入留批次记录（总数、创建、跳过、失败与错误明细），便于复盘与幂等重试。
--   编号 IMP-001 起全局自增，软删不复用。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_case_import_batch (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no    TEXT NOT NULL,
    project_id  INTEGER NOT NULL,
    file_name   TEXT NOT NULL DEFAULT '',
    format      TEXT NOT NULL DEFAULT '',
    mode        TEXT NOT NULL DEFAULT 'apply',
    status      TEXT NOT NULL DEFAULT 'done',
    total       INTEGER NOT NULL DEFAULT 0,
    created     INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]',
    created_by  TEXT NOT NULL DEFAULT 'local',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_case_import_batch_no
    ON biz_case_import_batch(batch_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_case_import_batch_project
    ON biz_case_import_batch(project_id, created_at);
