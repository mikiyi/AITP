-- =====================================================================
-- AITP 迁移 066：P2 定时调度
--   按固定间隔调度 场景/脚本 执行，复用 job_queue 投递（并发上限/隔离/取消原有）。
--   CI webhook 触发原有（GAP-24），本表只补「定时」一类触发源。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_test_schedule (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL,
    name            TEXT NOT NULL,
    target_type     TEXT NOT NULL DEFAULT 'scenario',
    target_id       INTEGER NOT NULL,
    interval_minutes INTEGER NOT NULL DEFAULT 60,
    enabled         INTEGER NOT NULL DEFAULT 1,
    next_run_at     TEXT,
    last_run_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_test_schedule_project ON biz_test_schedule(project_id);