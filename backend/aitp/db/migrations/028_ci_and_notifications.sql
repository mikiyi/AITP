-- =====================================================================
-- AITP 迁移 028：CI 触发令牌 + Webhook 通知
--   1) sys_ci_token：CI 调用 POST /api/v1/ci/runs 的令牌，只存哈希与展示前缀。
--   2) sys_notification_channel：Webhook 渠道，project_id 为空表示全局生效。
--   3) sys_notification_log：每次投递的请求体、结果与重试次数，失败可查。
-- =====================================================================

CREATE TABLE IF NOT EXISTS sys_ci_token (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL DEFAULT '',
    token_prefix TEXT NOT NULL DEFAULT '',
    token_hash   TEXT NOT NULL,
    project_id   INTEGER REFERENCES biz_project(id),
    enabled      INTEGER NOT NULL DEFAULT 1,
    last_used_at TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sys_ci_token_hash ON sys_ci_token(token_hash);

CREATE TABLE IF NOT EXISTS sys_notification_channel (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL DEFAULT '',
    project_id   INTEGER REFERENCES biz_project(id),
    webhook_url  TEXT NOT NULL DEFAULT '',
    events_json  TEXT NOT NULL DEFAULT '[]',
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    is_deleted   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sys_notification_channel_project
    ON sys_notification_channel(project_id);

CREATE TABLE IF NOT EXISTS sys_notification_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id    INTEGER REFERENCES sys_notification_channel(id),
    channel_name  TEXT NOT NULL DEFAULT '',
    project_id    INTEGER,
    event         TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    payload_json  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    http_status   INTEGER,
    response_text TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    task_id       INTEGER REFERENCES sys_task(id),
    created_at    TEXT NOT NULL,
    finished_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_sys_notification_log_event
    ON sys_notification_log(event, created_at);
CREATE INDEX IF NOT EXISTS idx_sys_notification_log_status
    ON sys_notification_log(status);
