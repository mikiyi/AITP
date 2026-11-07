-- =====================================================================
-- AITP 迁移 031：自动整理的真实去重语义
--   1. 合并提案审核通过后，被合并条目转为 merged 并从检索/统计中退出，
--      merged_into 指向保留条目，仍可直接打开查看溯源；
--   2. 合并目标记录在版本上，审核前不改变任何条目的正式状态。
-- =====================================================================

ALTER TABLE biz_knowledge_article ADD COLUMN merged_into INTEGER;

CREATE INDEX IF NOT EXISTS idx_biz_knowledge_article_merged
    ON biz_knowledge_article(merged_into);

ALTER TABLE biz_knowledge_version ADD COLUMN merge_targets_json TEXT;
