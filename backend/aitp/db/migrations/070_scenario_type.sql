-- =====================================================================
-- AITP 迁移 070：接口场景升级为「场景自动化」（Web / App / 接口三类）
--   原 biz_api_scenario 只承载接口串联场景；升级后按 scenario_type 区分
--   web=浏览器场景 / app=App 场景（占位，引擎待接入） / api=接口场景。
--   存量数据全部归为 api，保持既有行为不变。
-- =====================================================================

ALTER TABLE biz_api_scenario ADD COLUMN scenario_type TEXT NOT NULL DEFAULT 'api';

CREATE INDEX IF NOT EXISTS idx_biz_api_scenario_type
    ON biz_api_scenario(project_id, scenario_type) WHERE is_deleted = 0;
