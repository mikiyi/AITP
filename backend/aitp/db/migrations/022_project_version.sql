-- =====================================================================
-- AITP 迁移 022：项目版本号
--   软件工程项目有版本号概念。新建 biz_project_version 作为项目版本的
--   主数据（一个项目下可建多套版本，如 v1.0 / v2.0），各测试资产通过
--   version_id 关联到具体版本。
--   version_id 可空：空表示「未分配版本」，存量资产不必强制归版本，
--   新建资产也不强制填，留待用户按需补录。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_project_version (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES biz_project(id),
    version_code  TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'planning',
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_project_version_code
    ON biz_project_version(project_id, version_code) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_project_version_project
    ON biz_project_version(project_id);

-- 各测试资产补 version_id（可空，空 = 未分配版本）
ALTER TABLE biz_requirement    ADD COLUMN version_id INTEGER REFERENCES biz_project_version(id);
ALTER TABLE biz_test_case      ADD COLUMN version_id INTEGER REFERENCES biz_project_version(id);
ALTER TABLE biz_bug            ADD COLUMN version_id INTEGER REFERENCES biz_project_version(id);
ALTER TABLE biz_report         ADD COLUMN version_id INTEGER REFERENCES biz_project_version(id);
ALTER TABLE biz_automation_script ADD COLUMN version_id INTEGER REFERENCES biz_project_version(id);
ALTER TABLE biz_test_data_batch ADD COLUMN version_id INTEGER REFERENCES biz_project_version(id);
ALTER TABLE biz_sql_analysis   ADD COLUMN version_id INTEGER REFERENCES biz_project_version(id);

CREATE INDEX IF NOT EXISTS idx_biz_requirement_version  ON biz_requirement(version_id);
CREATE INDEX IF NOT EXISTS idx_biz_test_case_version    ON biz_test_case(version_id);
CREATE INDEX IF NOT EXISTS idx_biz_bug_version          ON biz_bug(version_id);
CREATE INDEX IF NOT EXISTS idx_biz_report_version       ON biz_report(version_id);
CREATE INDEX IF NOT EXISTS idx_biz_automation_version   ON biz_automation_script(version_id);
CREATE INDEX IF NOT EXISTS idx_biz_test_data_version    ON biz_test_data_batch(version_id);
CREATE INDEX IF NOT EXISTS idx_biz_sql_version          ON biz_sql_analysis(version_id);
