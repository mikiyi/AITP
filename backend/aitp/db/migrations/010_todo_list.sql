-- =====================================================================
-- AITP 迁移 010：任务清单
--   biz_todo：本机待办事项，支持优先级 / 到期日 / 分类 / 子任务 / 进度
--   单用户本地表，不进 AI 闭环，但可关联到项目与功能点，
--   这样「今天要做的事」和「正在测的东西」在同一台机器上对得上。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_todo (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    todo_no       TEXT NOT NULL,
    parent_id     INTEGER NOT NULL DEFAULT 0,
    project_id    INTEGER REFERENCES biz_project(id),
    module_id     INTEGER REFERENCES biz_module(id),
    title         TEXT NOT NULL,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    priority      TEXT NOT NULL DEFAULT 'medium',
    category      TEXT,
    tags          TEXT,
    due_date      TEXT,
    progress      INTEGER NOT NULL DEFAULT 0,
    is_pinned     INTEGER NOT NULL DEFAULT 0,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    completed_at  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_todo_no ON biz_todo(todo_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_todo_status ON biz_todo(status);
CREATE INDEX IF NOT EXISTS idx_biz_todo_priority ON biz_todo(priority);
CREATE INDEX IF NOT EXISTS idx_biz_todo_due ON biz_todo(due_date);
CREATE INDEX IF NOT EXISTS idx_biz_todo_category ON biz_todo(category);
CREATE INDEX IF NOT EXISTS idx_biz_todo_parent ON biz_todo(parent_id);
CREATE INDEX IF NOT EXISTS idx_biz_todo_project ON biz_todo(project_id);
