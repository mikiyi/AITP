-- =====================================================================
-- AITP 迁移 042：归一历史需求状态
--   早期演示数据往 biz_requirement 写过 'reviewed'，它不在需求状态枚举
--   （draft / reviewing / approved / implemented / rejected）里，列表下拉
--   因此既筛不到、也显示不出中文。这里统一归一到「已通过」。
-- =====================================================================

UPDATE biz_requirement
SET status = 'approved'
WHERE status = 'reviewed';
