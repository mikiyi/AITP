-- =====================================================================
-- AITP 迁移 018：快捷入口使用计数 + 导航未读角标
--   sys_quick_entry_usage：首页功能快捷入口的点击次数，用于按使用频率取前 10
--   sys_nav_badge        ：导航各资产分区的未读提醒数（AI 产出一键添加后 +1，
--                          进入对应页面查看后清零）
-- =====================================================================

CREATE TABLE IF NOT EXISTS sys_quick_entry_usage (
    entry_key    TEXT PRIMARY KEY,
    use_count    INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sys_nav_badge (
    section    TEXT PRIMARY KEY,
    unread     INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
