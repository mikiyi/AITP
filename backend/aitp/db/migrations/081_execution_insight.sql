-- =====================================================================
-- AITP 迁移 081：执行可信度（V2.6.0）
--   1) 接口执行记录补失败归因：真 Bug / 断言错误 / 环境问题 / 数据问题，
--      归因结论与置信度随执行落库，报告与修复建议据此区分「该不该改资产」；
--   2) 补重试与 flaky：同一条用例重试后通过会被标记为不稳定，
--      与真实失败分列统计，避免把环境抖动当成回归失败。
-- =====================================================================

ALTER TABLE biz_interface_execution ADD COLUMN failure_kind TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_interface_execution ADD COLUMN failure_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_interface_execution ADD COLUMN failure_confidence REAL NOT NULL DEFAULT 0;
ALTER TABLE biz_interface_execution ADD COLUMN attribution_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE biz_interface_execution ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE biz_interface_execution ADD COLUMN flaky INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_interface_execution_failure_kind
    ON biz_interface_execution(failure_kind);

ALTER TABLE biz_test_run_case ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE biz_test_run_case ADD COLUMN flaky INTEGER NOT NULL DEFAULT 0;
