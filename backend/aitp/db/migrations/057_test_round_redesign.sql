-- =====================================================================
-- AITP 迁移 057：测试轮次业务化改造（一期：类型 / 计划时间窗 / 范围预设 / 轮次模板）
--   biz_test_run 增加：
--     round_type    轮次类型 smoke/full/special/regression（冒烟/全覆盖/专项/回归）
--     goal          核心目标（按类型带默认值，可改）
--     planned_start / planned_end  计划时间窗（区别于实际 started/finished）
--     scope_preset  圈选范围预设键，供「按上轮结果刷新范围」复算
--     seq           轮次在计划内的顺序号（模板生成时递增）
--   门禁联动与 checklist 必测项集为二期，不在本迁移。
-- =====================================================================

ALTER TABLE biz_test_run ADD COLUMN round_type TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_test_run ADD COLUMN goal TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_test_run ADD COLUMN planned_start TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_test_run ADD COLUMN planned_end TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_test_run ADD COLUMN scope_preset TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_test_run ADD COLUMN seq INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_biz_test_run_plan_seq
    ON biz_test_run(plan_id, seq) WHERE is_deleted = 0;