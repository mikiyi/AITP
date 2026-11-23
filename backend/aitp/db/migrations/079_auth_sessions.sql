-- =====================================================================
-- AITP 迁移 079：登录态档案（四期专项 F）
--   按「项目 + 环境 + 角色」保存浏览器认证态（Playwright storageState：cookies + localStorage）。
--   文件只落本机 data/auth-sessions/，含会话 cookie，不进整包备份 / 项目资产包 / 导出。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_auth_session (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL,
    env_id       INTEGER,
    role         TEXT NOT NULL DEFAULT 'default',
    file_name    TEXT NOT NULL DEFAULT '',
    cookie_count INTEGER NOT NULL DEFAULT 0,
    origin_count INTEGER NOT NULL DEFAULT 0,
    expires_at   TEXT NOT NULL DEFAULT '',
    expired      INTEGER NOT NULL DEFAULT 0,
    note         TEXT NOT NULL DEFAULT '',
    last_used_at TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    is_deleted   INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_auth_session_scope
    ON biz_auth_session(project_id, COALESCE(env_id, 0), role) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_auth_session_project
    ON biz_auth_session(project_id, env_id);
