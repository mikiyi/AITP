-- =====================================================================
-- AITP 迁移 025：任务队列 + 测试环境绑定执行
--   1) sys_task：持久化轻量任务队列，支持排队、并发上限、取消、重试、
--      重启后恢复。自动化脚本等后台作业统一走这里，避免并发把服务拖死。
--   2) biz_test_env.env_kind：区分开发/测试/预发/生产，生产环境执行前二次确认。
--   3) 自动化脚本与执行记录补 env_id，执行时可带环境并反查。
-- =====================================================================

CREATE TABLE IF NOT EXISTS sys_task (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'queued',
    priority         INTEGER NOT NULL DEFAULT 100,
    payload_json     TEXT NOT NULL DEFAULT '{}',
    result_json      TEXT NOT NULL DEFAULT '{}',
    error_message    TEXT NOT NULL DEFAULT '',
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 1,
    progress         INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    started_at       TEXT,
    finished_at      TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sys_task_status ON sys_task(status);
CREATE INDEX IF NOT EXISTS idx_sys_task_kind ON sys_task(kind);
CREATE INDEX IF NOT EXISTS idx_sys_task_queue ON sys_task(status, priority, id);

ALTER TABLE biz_test_env ADD COLUMN env_kind TEXT NOT NULL DEFAULT 'test';

ALTER TABLE biz_automation_script ADD COLUMN env_id INTEGER REFERENCES biz_test_env(id);

ALTER TABLE biz_automation_run ADD COLUMN env_id INTEGER REFERENCES biz_test_env(id);
ALTER TABLE biz_automation_run ADD COLUMN task_id INTEGER REFERENCES sys_task(id);

CREATE INDEX IF NOT EXISTS idx_biz_automation_run_task ON biz_automation_run(task_id);
