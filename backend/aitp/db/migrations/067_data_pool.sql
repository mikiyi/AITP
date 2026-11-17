-- =====================================================================
-- AITP 迁移 067：残留子项 独立数据池（跨用例/场景共享）
--   数据池按 项目/名称 存内容（内联或文件路径），用例/场景用 pool:<名称> 引用，
--   解析复用 case_test_data，不另造解析器。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_data_pool (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL,
    name          TEXT NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_data_pool_project ON biz_data_pool(project_id);