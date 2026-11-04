-- =====================================================================
-- AITP 迁移 008：工作台编排资产 + 案例验证沙箱
--   sys_workbench_entry：每个 AI 入口的触发词、必填项、知识映射、工作流步骤
--   sys_validation_case：固定案例的输入与断言，发布前回归用
-- =====================================================================

CREATE TABLE IF NOT EXISTS sys_workbench_entry (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL,
    name          TEXT NOT NULL,
    capability    TEXT NOT NULL,
    description   TEXT,
    triggers_json TEXT NOT NULL DEFAULT '[]',
    required_json TEXT NOT NULL DEFAULT '[]',
    knowledge_json TEXT NOT NULL DEFAULT '[]',
    workflow_json TEXT NOT NULL DEFAULT '[]',
    version       INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'published',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_sys_workbench_entry_code ON sys_workbench_entry(code);
CREATE INDEX IF NOT EXISTS idx_sys_workbench_entry_status ON sys_workbench_entry(status);

CREATE TABLE IF NOT EXISTS sys_validation_case (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT,
    input_text      TEXT NOT NULL,
    capability      TEXT,
    variables_json  TEXT NOT NULL DEFAULT '{}',
    expected_routes_json TEXT NOT NULL DEFAULT '[]',
    expected_missing_json TEXT NOT NULL DEFAULT '[]',
    expected_rules_json  TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'published',
    last_result_json TEXT NOT NULL DEFAULT '{}',
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sys_validation_case_status ON sys_validation_case(status);
