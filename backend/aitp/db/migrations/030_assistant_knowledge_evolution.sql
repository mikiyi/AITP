-- =====================================================================
-- AITP 迁移 030：知识库问答助手自演进
--   1. 待审池 / 正式库补问答溯源字段（提问时间、回答模式、模型、引用）；
--      来源类型新增 assistant，审核者据此判断是否是模型幻觉；
--   2. 正式库补自动整理状态，状态必须落库：进程被杀后要能回退到「待自动整理」；
--   3. 待审池按内容指纹去重，同一答案重复点击只入池一次。
-- =====================================================================

ALTER TABLE biz_knowledge_draft ADD COLUMN answered_at TEXT;
ALTER TABLE biz_knowledge_draft ADD COLUMN answer_mode TEXT;
ALTER TABLE biz_knowledge_draft ADD COLUMN answer_model TEXT;
ALTER TABLE biz_knowledge_draft ADD COLUMN refs_json TEXT;
ALTER TABLE biz_knowledge_draft ADD COLUMN review_required INTEGER NOT NULL DEFAULT 0;
ALTER TABLE biz_knowledge_draft ADD COLUMN content_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_knowledge_draft_hash
    ON biz_knowledge_draft(content_hash)
    WHERE content_hash IS NOT NULL AND content_hash <> '' AND is_deleted = 0;

ALTER TABLE biz_knowledge_article ADD COLUMN answered_at TEXT;
ALTER TABLE biz_knowledge_article ADD COLUMN answer_mode TEXT;
ALTER TABLE biz_knowledge_article ADD COLUMN answer_model TEXT;
ALTER TABLE biz_knowledge_article ADD COLUMN refs_json TEXT;
ALTER TABLE biz_knowledge_article ADD COLUMN review_required INTEGER NOT NULL DEFAULT 0;
-- '' 表示不适用（非问答助手来源）；pending/running/done 三态驱动「待自动整理」按钮
ALTER TABLE biz_knowledge_article ADD COLUMN organize_status TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_knowledge_article ADD COLUMN organize_task_id INTEGER;
ALTER TABLE biz_knowledge_article ADD COLUMN organize_at TEXT;

CREATE INDEX IF NOT EXISTS idx_biz_knowledge_article_organize
    ON biz_knowledge_article(source_type, organize_status);
