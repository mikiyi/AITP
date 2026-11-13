-- =====================================================================
-- AITP 迁移 058：回填自动化脚本空框架
--   AI 产出早期入库未带框架，列表「框架」列显示「-」。按类别落默认框架：
--   web→selenium / api→requests / performance→jmeter / app→appium(鸿蒙→hypium)。
--   仅回填 framework 为空的行，不覆盖用户已选框架。
-- =====================================================================

UPDATE biz_automation_script
SET framework = CASE
    WHEN category = 'web' THEN 'selenium'
    WHEN category = 'api' THEN 'requests'
    WHEN category = 'performance' THEN 'jmeter'
    WHEN category = 'app' AND app_platforms LIKE '%hos%' THEN 'hypium'
    WHEN category = 'app' THEN 'appium'
    ELSE framework
END
WHERE (framework IS NULL OR framework = '')
  AND category IN ('web', 'api', 'performance', 'app');