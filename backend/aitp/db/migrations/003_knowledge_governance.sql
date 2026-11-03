-- =====================================================================
-- AITP 迁移 003：四级模块树 + AI 能力绑定 + 知识治理（待审池/版本/引用保护）
-- 建表顺序遵守外键依赖：biz_module -> 能力绑定 -> 待审池/版本/引用
-- =====================================================================

-- ---------------------------------------------------------------------
-- 四级模块树：项目 → 模块 → 子模块 → 功能
-- 用邻接表 + 派生 depth 实现，层级不硬编码，节点可增删改、可换父
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS biz_module (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES biz_project(id),
    parent_id   INTEGER REFERENCES biz_module(id),
    node_type   TEXT NOT NULL DEFAULT 'module',   -- module/submodule/feature
    name        TEXT NOT NULL,
    code        TEXT,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_module_project ON biz_module(project_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_biz_module_type    ON biz_module(node_type);

-- ---------------------------------------------------------------------
-- AI 能力挂在功能点上：每个节点可启用若干能力并指定专属提示词与默认参数
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS biz_module_ai_capability (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id   INTEGER NOT NULL REFERENCES biz_module(id),
    capability  TEXT NOT NULL,
    prompt_code TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    config_json TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_module_capability ON biz_module_ai_capability(module_id, capability);
CREATE INDEX IF NOT EXISTS idx_biz_module_capability_module ON biz_module_ai_capability(module_id);

-- ---------------------------------------------------------------------
-- 知识待审池：所有 AI 产出物先进这里展示，审核通过才转正式库成为数字资产
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS biz_knowledge_draft (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL,
    content        TEXT NOT NULL,
    summary        TEXT,
    category       TEXT,
    tags           TEXT,
    module_id      INTEGER REFERENCES biz_module(id),
    source_type    TEXT NOT NULL DEFAULT 'ai_output',
    ref_type       TEXT,                -- task/session/report/case/bug
    ref_id         INTEGER,
    ref_label      TEXT,
    producer       TEXT,                -- 产出该内容的 AI 能力 code
    review_status  TEXT NOT NULL DEFAULT 'pending',   -- pending/approved/rejected
    reviewer       TEXT,
    review_comment TEXT,
    reviewed_at    TEXT,
    article_id     INTEGER REFERENCES biz_knowledge_article(id),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    is_deleted     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_knowledge_draft_status ON biz_knowledge_draft(review_status, created_at);
CREATE INDEX IF NOT EXISTS idx_biz_knowledge_draft_module ON biz_knowledge_draft(module_id);

-- ---------------------------------------------------------------------
-- 知识版本：每次修改产生新版本，审核通过后才写回 article 正文
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS biz_knowledge_version (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id     INTEGER NOT NULL REFERENCES biz_knowledge_article(id),
    version        INTEGER NOT NULL,
    title          TEXT NOT NULL,
    content        TEXT NOT NULL,
    summary        TEXT,
    category       TEXT,
    tags           TEXT,
    review_status  TEXT NOT NULL DEFAULT 'published',
    reviewer       TEXT,
    review_comment TEXT,
    reviewed_at    TEXT,
    change_log     TEXT,
    checksum       TEXT,
    created_by     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_knowledge_version ON biz_knowledge_version(article_id, version);
CREATE INDEX IF NOT EXISTS idx_biz_knowledge_version_article ON biz_knowledge_version(article_id);
CREATE INDEX IF NOT EXISTS idx_biz_knowledge_version_status  ON biz_knowledge_version(review_status);

-- ---------------------------------------------------------------------
-- 引用登记：被下游引用的知识条目禁止撤销 / 删除
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS biz_knowledge_ref (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES biz_knowledge_article(id),
    ref_type   TEXT NOT NULL,           -- report/article/task/case/bug/module
    ref_id     INTEGER,
    ref_label  TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_knowledge_ref ON biz_knowledge_ref(article_id, ref_type, ref_id);
CREATE INDEX IF NOT EXISTS idx_biz_knowledge_ref_article ON biz_knowledge_ref(article_id);

-- ---------------------------------------------------------------------
-- 正式知识库扩展列
-- ---------------------------------------------------------------------
ALTER TABLE biz_knowledge_article ADD COLUMN module_id INTEGER REFERENCES biz_module(id);
ALTER TABLE biz_knowledge_article ADD COLUMN review_status TEXT NOT NULL DEFAULT 'published';
ALTER TABLE biz_knowledge_article ADD COLUMN current_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE biz_knowledge_article ADD COLUMN draft_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE biz_knowledge_article ADD COLUMN file_path TEXT;
ALTER TABLE biz_knowledge_article ADD COLUMN content_checksum TEXT;

CREATE INDEX IF NOT EXISTS idx_biz_knowledge_article_status ON biz_knowledge_article(review_status);
CREATE INDEX IF NOT EXISTS idx_biz_knowledge_article_module ON biz_knowledge_article(module_id);

-- ---------------------------------------------------------------------
-- 业务表挂功能点
-- ---------------------------------------------------------------------
ALTER TABLE biz_test_case ADD COLUMN module_id INTEGER REFERENCES biz_module(id);
ALTER TABLE biz_bug ADD COLUMN module_id INTEGER REFERENCES biz_module(id);
ALTER TABLE biz_ai_task ADD COLUMN module_id INTEGER REFERENCES biz_module(id);
ALTER TABLE biz_ai_task ADD COLUMN capability TEXT;

CREATE INDEX IF NOT EXISTS idx_biz_test_case_module ON biz_test_case(module_id);
CREATE INDEX IF NOT EXISTS idx_biz_bug_module ON biz_bug(module_id);
CREATE INDEX IF NOT EXISTS idx_biz_ai_task_module ON biz_ai_task(module_id);

-- ---------------------------------------------------------------------
-- 系统配置默认值
-- ---------------------------------------------------------------------
INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('knowledge.storage_path', '', 'string', 'global', '知识库 Markdown 存放根目录，空则用默认目录', 1,
     datetime('now'), datetime('now')),
    ('knowledge.file_mode', 'mirror', 'string', 'global', 'mirror=双写 Markdown 文件 / db_only=仅数据库', 1,
     datetime('now'), datetime('now')),
    ('knowledge.auto_reindex', 'false', 'bool', 'global', '启动时是否自动扫描知识库目录重建索引', 1,
     datetime('now'), datetime('now')),
    ('module.default_types', 'module,submodule,feature', 'string', 'global', '模块树层级角色名（逗号分隔）', 1,
     datetime('now'), datetime('now'));
