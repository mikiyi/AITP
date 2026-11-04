-- =====================================================================
-- AITP 迁移 019：SQL 分析资产
--   AI 实验室「SQL 分析」的产出物需要一个可管理、可回看的资产目录，
--   与需求 / 缺陷 / 回归清单并列放在「测试资产」下：
--   sql_text 存被分析的 SQL，analysis 存完整的分析结论（Markdown）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_sql_analysis (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES biz_project(id),
    module_id   INTEGER REFERENCES biz_module(id),
    sql_no      TEXT NOT NULL,
    title       TEXT NOT NULL,
    sql_text    TEXT NOT NULL DEFAULT '',
    analysis    TEXT NOT NULL DEFAULT '',
    risk_level  TEXT NOT NULL DEFAULT 'none',
    source      TEXT NOT NULL DEFAULT 'manual',
    tags        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_sql_analysis_no
    ON biz_sql_analysis(sql_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_sql_analysis_project ON biz_sql_analysis(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_sql_analysis_module ON biz_sql_analysis(module_id);
