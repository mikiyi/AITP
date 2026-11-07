-- =====================================================================
-- AITP 迁移 027：自动化结果回写用例 / 轮次
--   biz_automation_script：脚本声明覆盖哪些用例（按编号或标签）
--   biz_automation_run   ：执行记录关联测试轮次与回写结论
--   biz_automation_case_result：每个自动化用例的映射与结果明细
-- =====================================================================

ALTER TABLE biz_automation_script ADD COLUMN case_match_mode TEXT NOT NULL DEFAULT 'case_no';
ALTER TABLE biz_automation_script ADD COLUMN case_match_value TEXT NOT NULL DEFAULT '';

ALTER TABLE biz_automation_run ADD COLUMN test_run_id INTEGER REFERENCES biz_test_run(id);
ALTER TABLE biz_automation_run ADD COLUMN writeback_status TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_automation_run ADD COLUMN writeback_summary TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS biz_automation_case_result (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_run_id  INTEGER NOT NULL REFERENCES biz_automation_run(id),
    test_run_id        INTEGER REFERENCES biz_test_run(id),
    run_case_id        INTEGER REFERENCES biz_test_run_case(id),
    case_id            INTEGER NOT NULL REFERENCES biz_test_case(id),
    case_no            TEXT NOT NULL DEFAULT '',
    test_name          TEXT NOT NULL DEFAULT '',
    result             TEXT NOT NULL DEFAULT 'failed',
    message            TEXT NOT NULL DEFAULT '',
    mapped_by          TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_biz_automation_case_result_run
    ON biz_automation_case_result(automation_run_id);
CREATE INDEX IF NOT EXISTS idx_biz_automation_case_result_test_run
    ON biz_automation_case_result(test_run_id);
CREATE INDEX IF NOT EXISTS idx_biz_automation_case_result_case
    ON biz_automation_case_result(case_id);
CREATE INDEX IF NOT EXISTS idx_biz_automation_run_test_run
    ON biz_automation_run(test_run_id);
