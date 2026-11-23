-- =====================================================================
-- 080 需求批量拆分导入（四期专项 D）
--   1. biz_requirement_import：记录一次文档导入的指纹、条数与已采纳数，
--      同一项目下同一文件指纹只留一条批次（软删后允许重新导入）。
--   2. biz_requirement.import_id：需求回指所属导入批次，便于追溯来源文档。
--   条目级幂等复用 052/075 已建的外部键唯一索引：
--   external_platform = 'ai-import'，external_id = '<指纹>#<序号>'，
--   重复采纳同一份拆分结果时不会重复落库。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_requirement_import (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES biz_project(id),
    fingerprint     TEXT NOT NULL DEFAULT '',
    file_name       TEXT NOT NULL DEFAULT '',
    item_count      INTEGER NOT NULL DEFAULT 0,
    adopted_count   INTEGER NOT NULL DEFAULT 0,
    chars           INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);

-- 指纹为空的是「手工勾选后补建批次」，不参与唯一约束，允许多条并存
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_requirement_import_fingerprint
    ON biz_requirement_import(project_id, fingerprint)
    WHERE fingerprint <> '' AND is_deleted = 0;

CREATE INDEX IF NOT EXISTS idx_biz_requirement_import_project
    ON biz_requirement_import(project_id, created_at);

ALTER TABLE biz_requirement ADD COLUMN import_id INTEGER;
