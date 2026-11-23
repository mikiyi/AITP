-- =====================================================================
-- AITP 迁移 077：知识库向量检索（四期专项 B）
--   个人版数据量用 SQLite 存向量（BLOB）+ 内存余弦召回即可，不引入向量数据库。
--   分块按 Markdown 标题层级 + 约 800 字窗口，与 knowledge_storage 落盘格式对齐。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_kb_embedding (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER NOT NULL,
    project_id   INTEGER,
    chunk_index  INTEGER NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    dim          INTEGER NOT NULL DEFAULT 0,
    vector       BLOB,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_kb_embedding_chunk
    ON biz_kb_embedding(article_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_kb_embedding_article
    ON biz_kb_embedding(article_id);
CREATE INDEX IF NOT EXISTS idx_kb_embedding_project
    ON biz_kb_embedding(project_id);

-- 知识条目的嵌入状态：none / partial / ready / stale
ALTER TABLE biz_knowledge_article ADD COLUMN embedding_state TEXT NOT NULL DEFAULT 'none';
