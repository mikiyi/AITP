-- =====================================================================
-- AITP 迁移 090：Eval 跑批生命周期补列（设计 §九）
--   cancel_requested：取消标记，worker 每完成一条检查，不做硬 kill；
--   last_error：reaper 置 failed / 执行异常时的原因（如 infra.stale_run）。
-- SQLite 不支持 IF NOT EXISTS 的 ADD COLUMN，重复执行由迁移版本表拦截。
-- =====================================================================

ALTER TABLE biz_eval_run ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
ALTER TABLE biz_eval_run ADD COLUMN last_error TEXT NOT NULL DEFAULT '';
