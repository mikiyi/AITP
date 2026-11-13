-- =====================================================================
-- AITP 迁移 054：执行矩阵（GAP-43）
--   biz_execution_matrix       一次矩阵调度：环境集合 × 端/浏览器，绑定轮次或脚本
--   biz_execution_matrix_cell  矩阵格子：一条环境 × 端/浏览器组合，执行状态与计数
--   biz_execution_matrix_result 格子内每条用例/脚本的结论与断言明细
--   biz_automation_run.matrix_cell_id 自动化执行反查所属格子
--   矩阵本身只是调度与留痕，真实执行仍走统一执行协议与任务队列。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_execution_matrix (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id         INTEGER NOT NULL REFERENCES biz_project(id),
    matrix_no          TEXT NOT NULL,
    name               TEXT NOT NULL,
    mode               TEXT NOT NULL DEFAULT 'interface',   -- interface / automation
    run_id             INTEGER REFERENCES biz_test_run(id),
    env_ids_json       TEXT NOT NULL DEFAULT '[]',
    script_ids_json    TEXT NOT NULL DEFAULT '[]',
    target_mode        TEXT NOT NULL DEFAULT 'default',     -- default / all
    targets_json       TEXT NOT NULL DEFAULT '[]',
    status             TEXT NOT NULL DEFAULT 'draft',       -- draft / queued / running / done / failed / cancelled
    cell_total         INTEGER NOT NULL DEFAULT 0,
    cell_done          INTEGER NOT NULL DEFAULT 0,
    summary_json       TEXT NOT NULL DEFAULT '{}',
    prod_confirmed_by  TEXT NOT NULL DEFAULT '',
    prod_confirmed_at  TEXT,
    error_message      TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    is_deleted         INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_execution_matrix_no
    ON biz_execution_matrix(matrix_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_execution_matrix_project
    ON biz_execution_matrix(project_id) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_execution_matrix_run
    ON biz_execution_matrix(run_id) WHERE is_deleted = 0;

CREATE TABLE IF NOT EXISTS biz_execution_matrix_cell (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    matrix_id     INTEGER NOT NULL REFERENCES biz_execution_matrix(id),
    env_id        INTEGER REFERENCES biz_test_env(id),
    env_name      TEXT NOT NULL DEFAULT '',
    env_kind      TEXT NOT NULL DEFAULT '',
    target        TEXT NOT NULL DEFAULT '',
    target_label  TEXT NOT NULL DEFAULT '',
    browser       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',   -- pending / running / done / failed / skipped
    task_id       INTEGER REFERENCES sys_task(id),
    total         INTEGER NOT NULL DEFAULT 0,
    passed        INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    started_at    TEXT,
    finished_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_execution_matrix_cell
    ON biz_execution_matrix_cell(matrix_id, env_id, target) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_execution_matrix_cell_matrix
    ON biz_execution_matrix_cell(matrix_id) WHERE is_deleted = 0;

CREATE TABLE IF NOT EXISTS biz_execution_matrix_result (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    matrix_id       INTEGER NOT NULL REFERENCES biz_execution_matrix(id),
    cell_id         INTEGER NOT NULL REFERENCES biz_execution_matrix_cell(id),
    case_id         INTEGER REFERENCES biz_test_case(id),
    run_case_id     INTEGER REFERENCES biz_test_run_case(id),
    case_no         TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    result          TEXT NOT NULL DEFAULT 'failed',   -- passed / failed / blocked / skipped
    status_code     INTEGER,
    duration_ms     INTEGER,
    message         TEXT NOT NULL DEFAULT '',
    assertions_json TEXT NOT NULL DEFAULT '[]',
    bug_id          INTEGER REFERENCES biz_bug(id),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_execution_matrix_result_matrix
    ON biz_execution_matrix_result(matrix_id) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_execution_matrix_result_cell
    ON biz_execution_matrix_result(cell_id) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_execution_matrix_result_case
    ON biz_execution_matrix_result(case_id) WHERE is_deleted = 0;

ALTER TABLE biz_automation_run ADD COLUMN matrix_cell_id INTEGER REFERENCES biz_execution_matrix_cell(id);

CREATE INDEX IF NOT EXISTS idx_biz_automation_run_matrix_cell
    ON biz_automation_run(matrix_cell_id) WHERE matrix_cell_id IS NOT NULL;
