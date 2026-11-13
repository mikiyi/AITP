-- =====================================================================
-- AITP 迁移 052：外部平台只读导入（三期 GAP-40）
--   只读单向：从外部平台拉取需求/用例/缺陷，绝不回写。
--   凭据走系统密钥库，库内只存引用与掩码；映射模板与导入批次留审计。
--   三类资产加 external_platform / external_id / external_url，按项目 + 平台
--   + 外部 id 去重。
-- =====================================================================

ALTER TABLE biz_requirement ADD COLUMN external_platform TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_requirement ADD COLUMN external_id TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_requirement ADD COLUMN external_url TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_biz_requirement_external
    ON biz_requirement(project_id, external_platform, external_id)
    WHERE external_id <> '' AND is_deleted = 0;

ALTER TABLE biz_test_case ADD COLUMN external_platform TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_test_case ADD COLUMN external_id TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_test_case ADD COLUMN external_url TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_test_case_external
    ON biz_test_case(project_id, external_platform, external_id)
    WHERE external_id <> '' AND is_deleted = 0;

ALTER TABLE biz_bug ADD COLUMN external_platform TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_bug ADD COLUMN external_id TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_bug ADD COLUMN external_url TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_bug_external
    ON biz_bug(project_id, external_platform, external_id)
    WHERE external_id <> '' AND is_deleted = 0;

CREATE TABLE IF NOT EXISTS biz_external_import_config (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES biz_project(id),
    platform        TEXT NOT NULL,
    base_url        TEXT NOT NULL,
    username        TEXT NOT NULL DEFAULT '',
    product_id      TEXT NOT NULL DEFAULT '',
    auth_ref        TEXT NOT NULL DEFAULT '',
    auth_mask       TEXT NOT NULL DEFAULT '',
    server_username TEXT NOT NULL DEFAULT '',
    extra_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_external_import_config
    ON biz_external_import_config(project_id, platform) WHERE is_deleted = 0;

CREATE TABLE IF NOT EXISTS biz_external_import_mapping (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES biz_project(id),
    platform    TEXT NOT NULL,
    name        TEXT NOT NULL,
    mapping_json TEXT NOT NULL DEFAULT '{}',
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_external_import_mapping
    ON biz_external_import_mapping(project_id, platform, name) WHERE is_deleted = 0;

CREATE TABLE IF NOT EXISTS biz_external_import_batch (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES biz_project(id),
    platform      TEXT NOT NULL,
    config_id     INTEGER REFERENCES biz_external_import_config(id),
    mapping_id    INTEGER REFERENCES biz_external_import_mapping(id),
    kinds         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'running',
    fetched_json  TEXT NOT NULL DEFAULT '{}',
    imported_json TEXT NOT NULL DEFAULT '{}',
    skipped_json  TEXT NOT NULL DEFAULT '{}',
    errors_json   TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_external_import_batch_project
    ON biz_external_import_batch(project_id, platform);
