-- 执行记录补一列：产出原文
-- 能力页要能回看每一次「历史产出」，只存首行摘要不够用。
-- 旧记录从会话消息里回填：仅当该会话只有一条助手消息时才能确定对应产出，
-- 多轮会话里最后一条消息可能属于后续执行，宁可不填也不张冠李戴。

ALTER TABLE biz_ai_record ADD COLUMN output_text TEXT NOT NULL DEFAULT '';

UPDATE biz_ai_record
SET output_text = (
    SELECT m.content
    FROM biz_ai_message m
    WHERE m.session_id = biz_ai_record.session_id
      AND m.role = 'assistant'
      AND m.is_deleted = 0
)
WHERE output_text = ''
  AND session_id IS NOT NULL
  AND (
      SELECT COUNT(*)
      FROM biz_ai_message m
      WHERE m.session_id = biz_ai_record.session_id
        AND m.role = 'assistant'
        AND m.is_deleted = 0
  ) = 1;
