-- =====================================================================
-- AITP 迁移 068：探索异常一键登记缺陷
--   异常步登记缺陷后把 biz_bug.id 回写步骤，明细页据此显示「已登记」并支持幂等重试；
--   证据挂载仍走 biz_execution_evidence.bug_id，不改变待审池审核转缺陷的既有治理。
-- =====================================================================

ALTER TABLE biz_exploration_step ADD COLUMN bug_id INTEGER REFERENCES biz_bug(id);

CREATE INDEX IF NOT EXISTS idx_biz_exploration_step_bug
    ON biz_exploration_step(bug_id) WHERE bug_id IS NOT NULL;
