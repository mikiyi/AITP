-- =====================================================================
-- AITP 迁移 036：测试环境资源指标监控地址
--   首页环境健康监测除可达性外，还展示应用/数据库服务器的 CPU、内存、磁盘。
--   本机目标直接由后端采；远程目标不装 agent 无法读硬件指标，因此留一个
--   可选监控地址（node_exporter /metrics 或 JSON 指标接口），留空则只做可达性。
-- =====================================================================

ALTER TABLE biz_test_env ADD COLUMN app_metrics_url TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_test_env ADD COLUMN db_metrics_url TEXT NOT NULL DEFAULT '';
