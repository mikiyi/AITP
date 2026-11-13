-- =====================================================================
-- AITP 迁移 053：知识 / 报告片段跨项目复用（三期 GAP-42）
--   知识提升为全局后 project_id 置空，source_project_id 保留来源；
--   跨项目只登记引用（biz_knowledge_ref.ref_type='project'），不复制正文。
--   报告片段（biz_report_fragment）同样只登记使用关系（biz_report_fragment_use），
--   报告渲染时实时取源片段正文，避免多处漂移。
-- =====================================================================

ALTER TABLE biz_knowledge_article ADD COLUMN source_project_id INTEGER REFERENCES biz_project(id);
ALTER TABLE biz_knowledge_article ADD COLUMN promoted_at TEXT;
ALTER TABLE biz_knowledge_article ADD COLUMN promoted_by TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_biz_knowledge_article_source_project
    ON biz_knowledge_article(source_project_id);

CREATE TABLE IF NOT EXISTS biz_report_fragment (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES biz_project(id),
    title            TEXT NOT NULL,
    section          TEXT NOT NULL DEFAULT 'overview',
    content          TEXT NOT NULL,
    source_report_id INTEGER REFERENCES biz_report(id),
    source_note      TEXT NOT NULL DEFAULT '',
    is_global        INTEGER NOT NULL DEFAULT 0,
    created_by       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    is_deleted       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_biz_report_fragment_project
    ON biz_report_fragment(project_id, is_global) WHERE is_deleted = 0;

CREATE TABLE IF NOT EXISTS biz_report_fragment_use (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fragment_id INTEGER NOT NULL REFERENCES biz_report_fragment(id),
    report_id   INTEGER NOT NULL REFERENCES biz_report(id),
    inserted_by TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_report_fragment_use
    ON biz_report_fragment_use(report_id, fragment_id) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_report_fragment_use_fragment
    ON biz_report_fragment_use(fragment_id) WHERE is_deleted = 0;
