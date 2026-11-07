-- =====================================================================
-- AITP 迁移 038：隔离审计补列
--   1. biz_ai_task / biz_knowledge_draft 补 project_id：AI 任务与待审产出要能
--      落进项目视图，否则项目页的「AI 任务数 / 待审核产出 / 最近任务」全是全库口径；
--   2. biz_todo / biz_regression 补 version_id：待办与回归清单此前只有版本自由
--      文本，按版本筛选静默失效，与其余资产的两级版本口径不一致。
--   存量回填只认「功能点属于哪个项目」这一条硬事实，回填不到的留 NULL，
--   宁可项目视图少显示，也不把别的项目的数据算进来。
-- =====================================================================

ALTER TABLE biz_ai_task ADD COLUMN project_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_biz_ai_task_project ON biz_ai_task(project_id, is_deleted);

UPDATE biz_ai_task SET project_id = (
    SELECT m.project_id FROM biz_module m WHERE m.id = biz_ai_task.module_id
) WHERE project_id IS NULL AND module_id IS NOT NULL;

ALTER TABLE biz_knowledge_draft ADD COLUMN project_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_biz_knowledge_draft_project ON biz_knowledge_draft(project_id, is_deleted);

UPDATE biz_knowledge_draft SET project_id = (
    SELECT m.project_id FROM biz_module m WHERE m.id = biz_knowledge_draft.module_id
) WHERE project_id IS NULL AND module_id IS NOT NULL;

ALTER TABLE biz_todo ADD COLUMN version_id INTEGER;
ALTER TABLE biz_regression ADD COLUMN version_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_biz_todo_version ON biz_todo(version_id, is_deleted);
CREATE INDEX IF NOT EXISTS idx_biz_regression_version ON biz_regression(version_id, is_deleted);
