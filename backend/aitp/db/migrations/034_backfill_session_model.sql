-- =====================================================================
-- AITP 迁移 034：回填历史会话的模型名
--   会话表一直有 model 列，但执行链路只把模型写进消息表，导致会话列表
--   「模型」列整片为空。新运行由代码回写，这里把历史数据按最近一条
--   助手消息补齐。重复执行只会补仍为空的行。
-- =====================================================================

UPDATE biz_ai_session
SET model = (
        SELECT m.model
        FROM biz_ai_message AS m
        WHERE m.session_id = biz_ai_session.id
          AND m.is_deleted = 0
          AND m.role = 'assistant'
          AND m.model IS NOT NULL
          AND m.model <> ''
        ORDER BY m.id DESC
        LIMIT 1
    )
WHERE model IS NULL
   OR model = '';
