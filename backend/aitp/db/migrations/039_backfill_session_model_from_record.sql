-- =====================================================================
-- AITP 迁移 039：补齐 034 漏掉的会话模型
--   034 只从助手消息回填，但更早的链路存在「有执行记录、没有消息」的会话，
--   这些会话的「模型」列至今为空。执行记录里的 model 同样是当次真实用过的模型，
--   这里按最近一条非空记录补上；仍补不到的（会话压根没跑成功过）保持为空。
-- =====================================================================

UPDATE biz_ai_session
SET model = (
        SELECT r.model
        FROM biz_ai_record AS r
        WHERE r.session_id = biz_ai_session.id
          AND r.is_deleted = 0
          AND r.model IS NOT NULL
          AND r.model <> ''
        ORDER BY r.id DESC
        LIMIT 1
    )
WHERE (model IS NULL OR model = '')
  AND EXISTS (
        SELECT 1
        FROM biz_ai_record AS r
        WHERE r.session_id = biz_ai_session.id
          AND r.is_deleted = 0
          AND r.model IS NOT NULL
          AND r.model <> ''
  );
