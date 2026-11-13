-- 060: AI 档案支持 Token Plan（套餐/订阅额度）模式。
--
-- 口径：
-- - billing_mode = 'api'        : 按量 API 模式（现状），按档案单价折算金额；
-- - billing_mode = 'token_plan' : Token 套餐/订阅额度模式，同一 OpenAI 兼容接口 + 套餐凭证，
--   按额度扣减 token、显示剩余，不按量计金额（cost_micros 落 NULL）。
-- - token_quota : 套餐总 token 额度（0 = 未设额度，只统计不扣减）；
-- - token_used  : 已消耗 token（每次调用按 usage.totalTokens 累加）。
-- 老库缺列时由迁移补齐，默认 'api' / 0，行为与现状完全一致。

ALTER TABLE sys_ai_profile ADD COLUMN billing_mode TEXT NOT NULL DEFAULT 'api';
ALTER TABLE sys_ai_profile ADD COLUMN token_quota INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sys_ai_profile ADD COLUMN token_used INTEGER NOT NULL DEFAULT 0;