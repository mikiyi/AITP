-- =====================================================================
-- AITP 迁移 029：通知投递记录补 updated_at
--   028 建表时漏了 updated_at，而仓储基类 insert/update 会统一写入该字段，
--   导致发送记录、手动测试通知直接报错。这里补列，不改历史数据。
-- =====================================================================

ALTER TABLE sys_notification_log ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';
