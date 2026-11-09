-- =====================================================================
-- AITP 迁移 046：CI Token 访问边界收敛（GAP-24）
--   给 sys_ci_token 增加有效期与吊销状态两列，把「Token 泄露即全权」收紧为
--   「哈希 + 有效期 + 吊销 + 路由白名单」四道关（白名单在服务层判定，不涉及库结构）。
--   expires_at：ISO8601 到期时间；为空表示不过期。
--   revoked_at：ISO8601 吊销时间；为空表示未吊销。
--   旧令牌（本迁移之前已存在的记录）两列默认 NULL → 不过期、未吊销，保持可用。
--   enabled=0 是既有的「一键吊销」手段，revoked_at 只是补充「何时被吊销」的展示与审计。
-- =====================================================================

ALTER TABLE sys_ci_token ADD COLUMN expires_at TEXT;
ALTER TABLE sys_ci_token ADD COLUMN revoked_at TEXT;

CREATE INDEX IF NOT EXISTS idx_sys_ci_token_expires ON sys_ci_token(expires_at);
