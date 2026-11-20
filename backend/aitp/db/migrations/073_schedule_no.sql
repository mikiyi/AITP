-- =====================================================================
-- AITP 迁移 073：定时调度编号（SCH-xxx）
--   自动化脚本的「已被引用」以定时调度引用为准：调度是脚本唯一的持久引用方。
--   给调度补唯一编号，脚本列表悬停浮显的就是这些 SCH 编号。
--   存量按 id 顺序回填，新记录走 next_no 取最小空缺号（与其余资产同口径）。
-- =====================================================================

ALTER TABLE biz_test_schedule ADD COLUMN schedule_no TEXT NOT NULL DEFAULT '';

UPDATE biz_test_schedule
SET schedule_no = 'SCH-' || printf('%03d', (
    SELECT COUNT(*) FROM biz_test_schedule b WHERE b.id <= biz_test_schedule.id
))
WHERE schedule_no = '';

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_test_schedule_no
    ON biz_test_schedule(schedule_no) WHERE is_deleted = 0;