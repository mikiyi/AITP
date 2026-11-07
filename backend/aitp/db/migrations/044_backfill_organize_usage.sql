-- =====================================================================
-- AITP 迁移 044：回填「知识库问答自动整理」的历史调用次数
--   041 建的 sys_prompt_usage 从今天起累加，看不到升级前的真实调用。
--   整理任务本来就落在 sys_task（kind='knowledge.organize'），成功的任务
--   一定调过模型（没有可整理条目时在调模型之前就失败了），按它回填。
--   取 max 是为了不覆盖升级后已经累加出来的次数。
-- =====================================================================

INSERT INTO sys_prompt_usage (prompt_code, use_count, last_used_at)
SELECT 'knowledge-organize',
       COUNT(*),
       COALESCE(MAX(t.finished_at), MAX(t.created_at))
FROM sys_task t
WHERE t.kind = 'knowledge.organize'
  AND t.status = 'success'
HAVING COUNT(*) > 0
ON CONFLICT(prompt_code) DO UPDATE SET
    use_count = MAX(sys_prompt_usage.use_count, excluded.use_count),
    last_used_at = excluded.last_used_at;
