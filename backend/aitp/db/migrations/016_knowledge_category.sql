-- =====================================================================
-- AITP 迁移 016：知识库分类中文化 + 自定义分类
--   分类值直接存中文名（文章/待审/版本三张表的 category 都是自由文本），
--   这张表只是「可选项清单」与默认排序，新增自定义分类往这里加一行即可。
--   历史数据里的英文编码统一翻译成中文，避免筛选与展示两套词。
-- =====================================================================

CREATE TABLE IF NOT EXISTS sys_knowledge_category (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_system  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_sys_knowledge_category_name
    ON sys_knowledge_category(name) WHERE is_deleted = 0;

INSERT INTO sys_knowledge_category (name, sort_order, is_system, created_at, updated_at)
SELECT '通用', 10, 1, '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z'
WHERE NOT EXISTS (SELECT 1 FROM sys_knowledge_category WHERE name = '通用');
INSERT INTO sys_knowledge_category (name, sort_order, is_system, created_at, updated_at)
SELECT '测试方法', 20, 1, '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z'
WHERE NOT EXISTS (SELECT 1 FROM sys_knowledge_category WHERE name = '测试方法');
INSERT INTO sys_knowledge_category (name, sort_order, is_system, created_at, updated_at)
SELECT '经验总结', 30, 1, '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z'
WHERE NOT EXISTS (SELECT 1 FROM sys_knowledge_category WHERE name = '经验总结');
INSERT INTO sys_knowledge_category (name, sort_order, is_system, created_at, updated_at)
SELECT '踩坑记录', 40, 1, '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z'
WHERE NOT EXISTS (SELECT 1 FROM sys_knowledge_category WHERE name = '踩坑记录');
INSERT INTO sys_knowledge_category (name, sort_order, is_system, created_at, updated_at)
SELECT '会议纪要', 50, 1, '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z'
WHERE NOT EXISTS (SELECT 1 FROM sys_knowledge_category WHERE name = '会议纪要');
INSERT INTO sys_knowledge_category (name, sort_order, is_system, created_at, updated_at)
SELECT '安全测试', 60, 1, '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z'
WHERE NOT EXISTS (SELECT 1 FROM sys_knowledge_category WHERE name = '安全测试');

-- 历史英文编码 → 中文分类
UPDATE biz_knowledge_article SET category = '通用'
    WHERE category IN ('general', '');
UPDATE biz_knowledge_article SET category = '测试方法' WHERE category = 'test_method';
UPDATE biz_knowledge_article SET category = '经验总结' WHERE category = 'experience';
UPDATE biz_knowledge_article SET category = '踩坑记录' WHERE category = 'pitfall';
UPDATE biz_knowledge_article SET category = '会议纪要' WHERE category = 'meeting';
UPDATE biz_knowledge_article SET category = '安全测试' WHERE category = 'security';

UPDATE biz_knowledge_draft SET category = '通用'
    WHERE category IN ('general', '');
UPDATE biz_knowledge_draft SET category = '测试方法' WHERE category = 'test_method';
UPDATE biz_knowledge_draft SET category = '经验总结' WHERE category = 'experience';
UPDATE biz_knowledge_draft SET category = '踩坑记录' WHERE category = 'pitfall';
UPDATE biz_knowledge_draft SET category = '会议纪要' WHERE category = 'meeting';
UPDATE biz_knowledge_draft SET category = '安全测试' WHERE category = 'security';

UPDATE biz_knowledge_version SET category = '通用'
    WHERE category IN ('general', '');
UPDATE biz_knowledge_version SET category = '测试方法' WHERE category = 'test_method';
UPDATE biz_knowledge_version SET category = '经验总结' WHERE category = 'experience';
UPDATE biz_knowledge_version SET category = '踩坑记录' WHERE category = 'pitfall';
UPDATE biz_knowledge_version SET category = '会议纪要' WHERE category = 'meeting';
UPDATE biz_knowledge_version SET category = '安全测试' WHERE category = 'security';
