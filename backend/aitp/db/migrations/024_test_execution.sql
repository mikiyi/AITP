-- =====================================================================
-- AITP 迁移 024：测试计划 / 测试轮次 / 轮次用例 / 发布准入
--   之前用例只能逐条在线执行，没有「一轮测试」的载体：不知道这轮测了哪些、
--   结果如何、失败有没有转缺陷、能不能据此判断能否发布。
--   本迁移补齐执行闭环的四层数据：
--     biz_test_plan       测试计划（长期存在的测试范围定义）
--     biz_test_run        测试轮次（一次具体执行，冻结当时的用例快照）
--     biz_test_run_case   轮次内逐条用例与快照、结果
--     biz_release_gate    发布准入结论（基于轮次结果 + 缺陷阈值）
--   结果同时回写 biz_case_execution，保留逐条执行流水。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_test_plan (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES biz_project(id),
    version_id       INTEGER REFERENCES biz_project_version(id),
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    owner            TEXT NOT NULL DEFAULT '',
    default_env_id   INTEGER REFERENCES biz_test_env(id),
    scope_json       TEXT NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    is_deleted       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_test_plan_project ON biz_test_plan(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_test_plan_version ON biz_test_plan(version_id);

CREATE TABLE IF NOT EXISTS biz_test_run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id       INTEGER REFERENCES biz_test_plan(id),
    project_id    INTEGER NOT NULL REFERENCES biz_project(id),
    version_id    INTEGER REFERENCES biz_project_version(id),
    env_id        INTEGER REFERENCES biz_test_env(id),
    name          TEXT NOT NULL,
    env           TEXT NOT NULL DEFAULT '',
    owner         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    remark        TEXT NOT NULL DEFAULT '',
    total         INTEGER NOT NULL DEFAULT 0,
    passed        INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    blocked       INTEGER NOT NULL DEFAULT 0,
    skipped       INTEGER NOT NULL DEFAULT 0,
    not_executed  INTEGER NOT NULL DEFAULT 0,
    started_at    TEXT,
    finished_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_test_run_project ON biz_test_run(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_test_run_plan ON biz_test_run(plan_id);
CREATE INDEX IF NOT EXISTS idx_biz_test_run_status ON biz_test_run(status);

-- 轮次用例保存「当时」的标题/步骤/预期，用例后续被改不影响历史轮次
CREATE TABLE IF NOT EXISTS biz_test_run_case (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES biz_test_run(id),
    case_id         INTEGER NOT NULL REFERENCES biz_test_case(id),
    seq             INTEGER NOT NULL DEFAULT 0,
    case_no         TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    case_type       TEXT NOT NULL DEFAULT 'functional',
    priority        TEXT NOT NULL DEFAULT 'P2',
    module_id       INTEGER,
    module          TEXT NOT NULL DEFAULT '',
    requirement_id  INTEGER,
    preconditions   TEXT NOT NULL DEFAULT '',
    steps_json      TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT NOT NULL DEFAULT '',
    result          TEXT NOT NULL DEFAULT 'not_executed',
    actual_result   TEXT NOT NULL DEFAULT '',
    remark          TEXT NOT NULL DEFAULT '',
    bug_id          INTEGER REFERENCES biz_bug(id),
    executed_by     TEXT NOT NULL DEFAULT '',
    executed_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_test_run_case
    ON biz_test_run_case(run_id, case_id) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_test_run_case_run ON biz_test_run_case(run_id);
CREATE INDEX IF NOT EXISTS idx_biz_test_run_case_case ON biz_test_run_case(case_id);
CREATE INDEX IF NOT EXISTS idx_biz_test_run_case_result ON biz_test_run_case(result);

CREATE TABLE IF NOT EXISTS biz_release_gate (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES biz_project(id),
    version_id     INTEGER REFERENCES biz_project_version(id),
    run_id         INTEGER REFERENCES biz_test_run(id),
    name           TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'blocked',
    thresholds_json TEXT NOT NULL DEFAULT '{}',
    result_json    TEXT NOT NULL DEFAULT '{}',
    conclusion     TEXT NOT NULL DEFAULT '',
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    is_deleted     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_release_gate_project ON biz_release_gate(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_release_gate_version ON biz_release_gate(version_id);
CREATE INDEX IF NOT EXISTS idx_biz_release_gate_run ON biz_release_gate(run_id);

-- 执行流水补轮次关联，便于从轮次追溯每一条真实执行记录
ALTER TABLE biz_case_execution ADD COLUMN run_id INTEGER REFERENCES biz_test_run(id);
ALTER TABLE biz_case_execution ADD COLUMN run_case_id INTEGER REFERENCES biz_test_run_case(id);

CREATE INDEX IF NOT EXISTS idx_biz_case_execution_run ON biz_case_execution(run_id);
