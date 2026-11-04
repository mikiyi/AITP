-- =====================================================================
-- AITP 迁移 011：能力目录重构
--   1) 需求评审 requirement_review 与 需求澄清 requirement_clarify 功能近似，
--      合并为「需求确认」requirement_confirm，历史绑定与记录一并归并。
--   2) 自动化脚本 / 性能脚本是新增能力，历史数据无对应记录，无需回填。
--   3) biz_module_ai_capability 上有 (module_id, capability) 唯一索引，
--      同一功能点同时绑过两个旧能力时，先删重复行再改编号，避免唯一约束冲突。
-- =====================================================================

DELETE FROM biz_module_ai_capability
WHERE capability IN ('requirement_review', 'requirement_clarify')
  AND id NOT IN (
      SELECT MIN(id)
      FROM biz_module_ai_capability
      WHERE capability IN ('requirement_review', 'requirement_clarify')
      GROUP BY module_id
  );

UPDATE biz_module_ai_capability
SET capability = 'requirement_confirm'
WHERE capability IN ('requirement_review', 'requirement_clarify');

UPDATE biz_ai_record
SET capability = 'requirement_confirm'
WHERE capability IN ('requirement_review', 'requirement_clarify');

UPDATE biz_ai_task
SET capability = 'requirement_confirm'
WHERE capability IN ('requirement_review', 'requirement_clarify');

UPDATE biz_ai_task
SET task_type = 'requirement_confirm'
WHERE task_type IN ('requirement_review', 'requirement_clarify');
