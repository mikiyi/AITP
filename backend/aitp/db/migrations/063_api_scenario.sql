-- =====================================================================
-- AITP 迁移 063：多接口串联场景自动化
--   场景 = 有序步骤（每步复用接口用例 extra 字段口径）+ 变量提取/传递 +
--   失败策略 + 数据驱动。执行复用 interface_runner，不另造 HTTP/断言引擎。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_api_scenario (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_no   TEXT NOT NULL,
    project_id    INTEGER NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    env_id        INTEGER,
    data_source   TEXT NOT NULL DEFAULT '',
    steps_json    TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL DEFAULT 'ready',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_scenario_project ON biz_api_scenario(project_id);

CREATE TABLE IF NOT EXISTS biz_api_scenario_run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_no        TEXT NOT NULL,
    scenario_id   INTEGER NOT NULL,
    project_id    INTEGER NOT NULL,
    env_id        INTEGER,
    passed        INTEGER NOT NULL DEFAULT 0,
    record_count  INTEGER NOT NULL DEFAULT 1,
    summary_json  TEXT NOT NULL DEFAULT '{}',
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_scenario_run_scenario ON biz_api_scenario_run(scenario_id);