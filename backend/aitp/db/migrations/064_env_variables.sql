-- =====================================================================
-- AITP 迁移 064：环境级变量（P1）
--   环境可携带 key/value 变量，注入接口/场景执行的 {{var}} 占位符；
--   项目级全局变量存 sys_config（api.global_vars.<projectId>），环境级覆盖全局。
-- =====================================================================

ALTER TABLE biz_test_env ADD COLUMN variables_json TEXT NOT NULL DEFAULT '{}';