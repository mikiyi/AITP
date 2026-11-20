-- =====================================================================
-- AITP 迁移 072：自动化脚本 / 场景步骤 数据库脚本下沉
--   三个引用字段存脚本编号 JSON 数组（["SQL-001","SQL-002"]）：
--     pre_db_refs    —— 执行前预处理（允许写：造数）
--     post_db_refs   —— 执行后处理（允许写：清理）
--     db_assert_refs —— 结果断言（只读查询，复用现有断言算子）
--   场景侧把引用写进 database 步骤的 extra，steps_json 原样存储。
-- =====================================================================

ALTER TABLE biz_automation_script ADD COLUMN pre_db_refs TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_automation_script ADD COLUMN post_db_refs TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_automation_script ADD COLUMN db_assert_refs TEXT NOT NULL DEFAULT '';
