-- =====================================================================
-- AITP 迁移 026：接口用例执行引擎
--   之前「接口测试」用例的 URL / 方法 / 请求头 / 断言只是扩展字段，后端
--   没有业务 HTTP 执行器。本迁移补两件事：
--   1) 轮次用例快照补 extra_json：发起轮次后即使用例被改，执行时仍用当时
--      的接口定义，保证历史可复现。
--   2) biz_interface_execution：保存每次真实请求的方法、地址、状态码、
--      耗时、响应片段与断言明细，失败原因可回溯。
-- =====================================================================

ALTER TABLE biz_test_run_case ADD COLUMN extra_json TEXT NOT NULL DEFAULT '{}';

UPDATE biz_test_run_case
SET extra_json = COALESCE(
    (SELECT c.extra_json FROM biz_test_case c WHERE c.id = biz_test_run_case.case_id),
    '{}'
)
WHERE extra_json = '{}';

CREATE TABLE IF NOT EXISTS biz_interface_execution (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         INTEGER NOT NULL REFERENCES biz_test_case(id),
    run_id          INTEGER REFERENCES biz_test_run(id),
    run_case_id     INTEGER REFERENCES biz_test_run_case(id),
    env_id          INTEGER REFERENCES biz_test_env(id),
    method          TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL DEFAULT '',
    status_code     INTEGER,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    result          TEXT NOT NULL DEFAULT 'failed',
    request_json    TEXT NOT NULL DEFAULT '{}',
    response_json   TEXT NOT NULL DEFAULT '{}',
    assertions_json TEXT NOT NULL DEFAULT '[]',
    error_message   TEXT NOT NULL DEFAULT '',
    executed_by     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_biz_interface_execution_case
    ON biz_interface_execution(case_id);
CREATE INDEX IF NOT EXISTS idx_biz_interface_execution_run
    ON biz_interface_execution(run_id);
CREATE INDEX IF NOT EXISTS idx_biz_interface_execution_run_case
    ON biz_interface_execution(run_case_id);
