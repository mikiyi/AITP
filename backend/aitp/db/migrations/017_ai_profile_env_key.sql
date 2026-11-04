-- =====================================================================
-- AITP 迁移 017：AI 档案支持从本地环境变量取 Key
--   use_env_key   ：开启后不用密钥库里保存的 Key，改从环境变量读取
--   env_key_name  ：环境变量名（必须与本机变量同名）
--   env_key_scope ：user=用户变量，system=系统变量
-- =====================================================================

ALTER TABLE sys_ai_profile ADD COLUMN use_env_key   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sys_ai_profile ADD COLUMN env_key_name  TEXT NOT NULL DEFAULT '';
ALTER TABLE sys_ai_profile ADD COLUMN env_key_scope TEXT NOT NULL DEFAULT 'user';
