-- =====================================================================
-- AITP 迁移 051：测试数据脱敏与样本池（三期 GAP-39）
--   样本：生产/脱敏前的原始数据只在本机内存中解析，落盘的是脱敏后的副本；
--         库内只存列结构、命中类型、方案版本、保留期与相对路径。
--   方案：字段级规则（mask / hash / random / enum / redact）可保存复用，
--         project_id 为空表示全局方案。
--   生命周期：expires_at 到期后由清理任务软删样本并删除脱敏副本。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_test_data_mask_scheme (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES biz_project(id),
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    rules_json  TEXT NOT NULL DEFAULT '{}',
    version     INTEGER NOT NULL DEFAULT 1,
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_test_data_mask_scheme_name
    ON biz_test_data_mask_scheme(COALESCE(project_id, 0), name) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_test_data_mask_scheme_project
    ON biz_test_data_mask_scheme(project_id) WHERE is_deleted = 0;

CREATE TABLE IF NOT EXISTS biz_test_data_sample (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id        INTEGER NOT NULL REFERENCES biz_project(id),
    name              TEXT NOT NULL,
    source_format     TEXT NOT NULL,
    original_filename TEXT NOT NULL DEFAULT '',
    row_count         INTEGER NOT NULL DEFAULT 0,
    column_count      INTEGER NOT NULL DEFAULT 0,
    columns_json      TEXT NOT NULL DEFAULT '[]',
    scheme_id         INTEGER REFERENCES biz_test_data_mask_scheme(id),
    scheme_version    INTEGER NOT NULL DEFAULT 1,
    detection_json    TEXT NOT NULL DEFAULT '{}',
    rules_json        TEXT NOT NULL DEFAULT '{}',
    rel_path          TEXT NOT NULL DEFAULT '',
    retention_days    INTEGER NOT NULL DEFAULT 30,
    expires_at        TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    is_deleted        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_test_data_sample_project
    ON biz_test_data_sample(project_id) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_test_data_sample_expires
    ON biz_test_data_sample(expires_at) WHERE is_deleted = 0;
