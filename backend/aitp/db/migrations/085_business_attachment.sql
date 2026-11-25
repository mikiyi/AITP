-- =====================================================================
-- AITP 迁移 085：业务对象持久附件
--   与 AI 入口的临时附件（data/attachments，用完即删）区分：
--   这里的附件跟缺陷 / 用例 / 轮次 / 探索会话 / 需求 / 知识条目一起长期保留，
--   文件落 data/business-attachments/<ownerType>/<ownerId>/。
--   kind=image 仅表示可在页面内直接预览的位图；SVG/HTML 等一律按普通附件下载。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_attachment (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER REFERENCES biz_project(id),
    owner_type    TEXT NOT NULL,
    owner_id      INTEGER NOT NULL,
    file_name     TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    mime          TEXT NOT NULL DEFAULT '',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'file',
    stored_name   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_attachment_owner
    ON biz_attachment(owner_type, owner_id) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_attachment_project
    ON biz_attachment(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_attachment_stored
    ON biz_attachment(stored_name) WHERE is_deleted = 0;
