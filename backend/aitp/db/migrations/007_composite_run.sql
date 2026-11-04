-- =====================================================================
-- AITP 迁移 007：多入口顺序执行
--   1) biz_ai_task 增加 parent_id，支持「一次输入 → 多入口顺序执行」的父子任务
--   2) biz_ai_step 记录每一步的真实轨迹（步骤名/状态/模型/耗时/错误）
--   3) biz_ai_record 补四段：原始输入、路由命中、步骤轨迹、模型用量
-- =====================================================================

ALTER TABLE biz_ai_task ADD COLUMN parent_id INTEGER REFERENCES biz_ai_task(id);
CREATE INDEX IF NOT EXISTS idx_biz_ai_task_parent ON biz_ai_task(parent_id);

CREATE TABLE IF NOT EXISTS biz_ai_step (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        INTEGER NOT NULL REFERENCES biz_ai_task(id),
    parent_task_id INTEGER REFERENCES biz_ai_task(id),
    capability     TEXT NOT NULL,
    step_order     INTEGER NOT NULL DEFAULT 0,
    step_name      TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    input_summary  TEXT,
    output_summary TEXT,
    model          TEXT,
    latency_ms     INTEGER,
    error_code     INTEGER,
    error_message  TEXT,
    started_at     TEXT,
    completed_at   TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    is_deleted     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_ai_step_task ON biz_ai_step(task_id);
CREATE INDEX IF NOT EXISTS idx_biz_ai_step_parent ON biz_ai_step(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_biz_ai_step_capability ON biz_ai_step(capability);

-- 执行记录补四段：原始输入 / 路由命中 / 步骤轨迹 / 模型用量
ALTER TABLE biz_ai_record ADD COLUMN input_json  TEXT NOT NULL DEFAULT '{}';
ALTER TABLE biz_ai_record ADD COLUMN route_json  TEXT NOT NULL DEFAULT '[]';
ALTER TABLE biz_ai_record ADD COLUMN usage_json  TEXT NOT NULL DEFAULT '{}';
ALTER TABLE biz_ai_record ADD COLUMN prompt_code TEXT;
ALTER TABLE biz_ai_record ADD COLUMN latency_ms  INTEGER;
ALTER TABLE biz_ai_record ADD COLUMN steps_json TEXT NOT NULL DEFAULT '[]';
