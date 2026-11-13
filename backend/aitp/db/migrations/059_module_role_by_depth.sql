-- =====================================================================
-- AITP 迁移 059：按深度回填模块树层级角色
--   历史缺陷（BUG-43）：模块树设置页选中对象丢字段，创建表单层级全部
--   默认 'module'，导致子模块/功能层角色与深度错位。层级角色名配置
--   「module,submodule,feature」本就按深度依次对应，这里统一回填：
--   深度1=module、深度2=submodule、深度3及以下=feature。
--   只处理未软删节点；软删历史保持原样。
-- =====================================================================

WITH RECURSIVE tree(id, depth) AS (
    SELECT id, 1 FROM biz_module
    WHERE parent_id IS NULL AND is_deleted = 0
    UNION ALL
    SELECT m.id, t.depth + 1
    FROM biz_module m JOIN tree t ON m.parent_id = t.id
    WHERE m.is_deleted = 0
)
UPDATE biz_module
SET node_type = CASE (SELECT t.depth FROM tree t WHERE t.id = biz_module.id)
    WHEN 1 THEN 'module'
    WHEN 2 THEN 'submodule'
    ELSE 'feature'
END
WHERE id IN (SELECT id FROM tree)
  AND is_deleted = 0;