-- =====================================================================
-- AITP 迁移 065：P1 #7 脚本版本/评审 + #8 元素库（Page Object）
--   脚本内容变更时自动快照旧版到版本表，支持差异对比与评审；
--   元素库按 项目/页面/名称 管理选择器，供 Web/App 脚本引用（不另造录制器，
--   录制回放复用探索性会话步骤转脚本）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_automation_script_version (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id     INTEGER NOT NULL,
    version_no    INTEGER NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    change_note   TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_script_version_script ON biz_automation_script_version(script_id);

CREATE TABLE IF NOT EXISTS biz_ui_element (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL,
    page          TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL,
    selector      TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'css',
    description   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ui_element_project ON biz_ui_element(project_id);