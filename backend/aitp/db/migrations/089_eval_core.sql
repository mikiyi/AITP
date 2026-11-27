-- =====================================================================
-- AITP 迁移 089：AI 质量评测内核（Eval 子包 · MVP 切片）
--   1) 评测集/用例：分层资产、软删、schema_version、可见性（防污染）；
--   2) 配置不可变：改了就是新行，run 里再存快照；
--   3) run 具幂等键（含用例集哈希）、租约与心跳：可续跑、可复原；
--   4) 指标单独成表：Wilson 区间与样本量可复算；
--   5) 门禁结论单列成表：一个 run 可被多条门禁求值。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_eval_suite (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES biz_project(id),
    name         TEXT    NOT NULL,
    target_type  TEXT    NOT NULL DEFAULT 'llm',
    description  TEXT    NOT NULL DEFAULT '',
    tags         TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'active',
    visibility   TEXT    NOT NULL DEFAULT 'private',
    version      INTEGER NOT NULL DEFAULT 1,
    case_count   INTEGER NOT NULL DEFAULT 0,
    creator      TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    is_deleted   INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_eval_suite_name
    ON biz_eval_suite(project_id, name) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_eval_suite_project ON biz_eval_suite(project_id, status);

CREATE TABLE IF NOT EXISTS biz_eval_case (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES biz_project(id),
    suite_id        INTEGER NOT NULL REFERENCES biz_eval_suite(id),
    case_no         TEXT    NOT NULL,
    category        TEXT    NOT NULL DEFAULT 'golden',
    title           TEXT    NOT NULL,
    input_json      TEXT    NOT NULL DEFAULT '{}',
    expected_type   TEXT    NOT NULL DEFAULT 'assert',
    expected_text   TEXT    NOT NULL DEFAULT '',
    rubric_json     TEXT    NOT NULL DEFAULT '[]',
    assertions_json TEXT    NOT NULL DEFAULT '{}',
    schema_version  INTEGER NOT NULL DEFAULT 1,
    relevant_docs   TEXT    NOT NULL DEFAULT '[]',
    graded_relevance TEXT   NOT NULL DEFAULT '[]',
    tools_expected  TEXT    NOT NULL DEFAULT '{}',
    severity        TEXT    NOT NULL DEFAULT 'S1',
    effective_date  TEXT    NOT NULL DEFAULT '',
    tags            TEXT    NOT NULL DEFAULT '',
    difficulty      INTEGER NOT NULL DEFAULT 2,
    source          TEXT    NOT NULL DEFAULT 'manual',
    source_ref      TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'active',
    creator         TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_eval_case_no
    ON biz_eval_case(suite_id, case_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_eval_case_suite ON biz_eval_case(suite_id, category, status);

CREATE TABLE IF NOT EXISTS biz_eval_config (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES biz_project(id),
    name          TEXT    NOT NULL,
    profile_id    INTEGER REFERENCES sys_ai_profile(id),
    provider      TEXT    NOT NULL DEFAULT '',
    model         TEXT    NOT NULL DEFAULT '',
    temperature   REAL    NOT NULL DEFAULT 0.0,
    top_p         REAL    NOT NULL DEFAULT 1.0,
    max_tokens    INTEGER NOT NULL DEFAULT 4096,
    seed          INTEGER,
    prompt_code   TEXT    NOT NULL DEFAULT '',
    prompt_version TEXT   NOT NULL DEFAULT '',
    retrieval_json TEXT   NOT NULL DEFAULT '{}',
    tools_json    TEXT    NOT NULL DEFAULT '[]',
    is_baseline   INTEGER NOT NULL DEFAULT 0,
    note          TEXT    NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_biz_eval_config_project ON biz_eval_config(project_id, is_deleted);

CREATE TABLE IF NOT EXISTS biz_eval_judge (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES biz_project(id),
    name            TEXT    NOT NULL,
    profile_id      INTEGER REFERENCES sys_ai_profile(id),
    prompt_code     TEXT    NOT NULL DEFAULT '',
    rubric_template TEXT    NOT NULL DEFAULT '[]',
    judge_configs_json TEXT NOT NULL DEFAULT '[]',
    strategy        TEXT    NOT NULL DEFAULT 'single',
    panel_size      INTEGER NOT NULL DEFAULT 1,
    temperature     REAL    NOT NULL DEFAULT 0.0,
    seed            INTEGER,
    agreement_rate  REAL,
    kappa           REAL,
    false_pass_rate REAL,
    calibrated_at   TEXT,
    calibration_size INTEGER NOT NULL DEFAULT 0,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    status          TEXT    NOT NULL DEFAULT 'active',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_biz_eval_judge_project ON biz_eval_judge(project_id, is_deleted);

CREATE TABLE IF NOT EXISTS biz_eval_run (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES biz_project(id),
    suite_id         INTEGER NOT NULL REFERENCES biz_eval_suite(id),
    suite_version    INTEGER NOT NULL DEFAULT 1,
    config_id        INTEGER REFERENCES biz_eval_config(id),
    judge_id         INTEGER REFERENCES biz_eval_judge(id),
    judge_mode       TEXT    NOT NULL DEFAULT 'rule',
    trigger_type     TEXT    NOT NULL DEFAULT 'manual',
    ablation_group   TEXT    NOT NULL DEFAULT '',
    config_snapshot  TEXT    NOT NULL DEFAULT '{}',
    judge_snapshot   TEXT    NOT NULL DEFAULT '{}',
    case_set_hash    TEXT    NOT NULL DEFAULT '',
    run_purpose      TEXT    NOT NULL DEFAULT 'run',
    rerun_of         INTEGER REFERENCES biz_eval_run(id),
    run_key          TEXT    NOT NULL DEFAULT '',
    status           TEXT    NOT NULL DEFAULT 'queued',
    total            INTEGER NOT NULL DEFAULT 0,
    skipped          INTEGER NOT NULL DEFAULT 0,
    passed           INTEGER NOT NULL DEFAULT 0,
    failed           INTEGER NOT NULL DEFAULT 0,
    errors           INTEGER NOT NULL DEFAULT 0,
    judge_invalid    INTEGER NOT NULL DEFAULT 0,
    success_rate     REAL,
    worker_token     TEXT    NOT NULL DEFAULT '',
    lease_expires_at TEXT,
    is_pinned        INTEGER NOT NULL DEFAULT 0,
    started_at       TEXT,
    finished_at      TEXT,
    heartbeat_at     TEXT,
    duration_ms      INTEGER NOT NULL DEFAULT 0,
    tokens_in        INTEGER NOT NULL DEFAULT 0,
    tokens_out       INTEGER NOT NULL DEFAULT 0,
    cost_micros      INTEGER NOT NULL DEFAULT 0,
    cost_source      TEXT    NOT NULL DEFAULT 'estimated',
    baseline_run_id  INTEGER REFERENCES biz_eval_run(id),
    gate_conclusion  TEXT    NOT NULL DEFAULT '',
    creator          TEXT,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_eval_run_key
    ON biz_eval_run(run_key) WHERE run_key <> '';
CREATE INDEX IF NOT EXISTS idx_biz_eval_run_suite ON biz_eval_run(suite_id, created_at desc);
CREATE INDEX IF NOT EXISTS idx_biz_eval_run_group ON biz_eval_run(ablation_group);

CREATE TABLE IF NOT EXISTS biz_eval_result (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES biz_eval_run(id),
    case_id        INTEGER NOT NULL REFERENCES biz_eval_case(id),
    case_no        TEXT    NOT NULL,
    output_text    TEXT    NOT NULL DEFAULT '',
    tool_trace     TEXT    NOT NULL DEFAULT '[]',
    retrieved_json TEXT    NOT NULL DEFAULT '[]',
    end_state      TEXT    NOT NULL DEFAULT '{}',
    attempt_count  INTEGER NOT NULL DEFAULT 1,
    fallback_used  INTEGER NOT NULL DEFAULT 0,
    steps          INTEGER NOT NULL DEFAULT 0,
    verdict        TEXT    NOT NULL DEFAULT 'skip',
    judge_valid    INTEGER NOT NULL DEFAULT 1,
    score          REAL,
    failure_reason TEXT    NOT NULL DEFAULT '',
    judge_json     TEXT    NOT NULL DEFAULT '{}',
    human_verdict  TEXT,
    human_score    REAL,
    human_by       TEXT,
    human_at       TEXT,
    latency_ms     INTEGER NOT NULL DEFAULT 0,
    tokens_in      INTEGER NOT NULL DEFAULT 0,
    tokens_out     INTEGER NOT NULL DEFAULT 0,
    cost_micros    INTEGER NOT NULL DEFAULT 0,
    cost_source    TEXT    NOT NULL DEFAULT 'estimated',
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_eval_result_case ON biz_eval_result(run_id, case_id);
CREATE INDEX IF NOT EXISTS idx_biz_eval_result_verdict ON biz_eval_result(run_id, verdict);

CREATE TABLE IF NOT EXISTS biz_eval_metric (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES biz_eval_run(id),
    metric_key  TEXT    NOT NULL,
    value       REAL,
    ci_lower    REAL,
    ci_upper    REAL,
    ci_method   TEXT    NOT NULL DEFAULT 'wilson',
    unit        TEXT    NOT NULL DEFAULT 'ratio',
    numerator   REAL,
    denominator REAL,
    sample_size INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT    NOT NULL DEFAULT '{}',
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_eval_metric_key ON biz_eval_metric(run_id, metric_key);

CREATE TABLE IF NOT EXISTS biz_eval_calibration (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    judge_id         INTEGER NOT NULL REFERENCES biz_eval_judge(id),
    run_id           INTEGER REFERENCES biz_eval_run(id),
    sample_size      INTEGER NOT NULL DEFAULT 0,
    agreement_before REAL,
    agreement_after  REAL,
    kappa_after      REAL,
    false_pass_rate  REAL,
    false_fail_rate  REAL,
    position_bias    REAL,
    verbosity_bias   REAL,
    detail_json      TEXT    NOT NULL DEFAULT '{}',
    schema_version   INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_biz_eval_calibration_judge ON biz_eval_calibration(judge_id, created_at desc);

CREATE TABLE IF NOT EXISTS biz_eval_gate (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES biz_project(id),
    suite_id        INTEGER NOT NULL REFERENCES biz_eval_suite(id),
    name            TEXT    NOT NULL,
    thresholds_json TEXT    NOT NULL DEFAULT '{}',
    is_hard         INTEGER NOT NULL DEFAULT 1,
    baseline_run_id INTEGER REFERENCES biz_eval_run(id),
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_run_id     INTEGER REFERENCES biz_eval_run(id),
    last_conclusion TEXT    NOT NULL DEFAULT '',
    last_evaluated_at TEXT,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_biz_eval_gate_project ON biz_eval_gate(project_id, suite_id);

CREATE TABLE IF NOT EXISTS biz_eval_gate_result (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_id       INTEGER NOT NULL REFERENCES biz_eval_gate(id),
    run_id        INTEGER NOT NULL REFERENCES biz_eval_run(id),
    conclusion    TEXT    NOT NULL DEFAULT '',
    dimension     TEXT    NOT NULL DEFAULT 'eval',
    failures_json TEXT    NOT NULL DEFAULT '[]',
    ci_snapshot   TEXT    NOT NULL DEFAULT '{}',
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_biz_eval_gate_result ON biz_eval_gate_result(gate_id, created_at desc);
CREATE INDEX IF NOT EXISTS idx_biz_eval_gate_result_run ON biz_eval_gate_result(run_id);
