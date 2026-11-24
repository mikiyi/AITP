-- V2.6.1：模型档案声明是否支持图片输入。
-- 默认 1 保持既有行为；标记为 0 时，带图请求会跳过图片并给出说明，文本链路不阻塞。
ALTER TABLE sys_ai_profile ADD COLUMN supports_vision INTEGER NOT NULL DEFAULT 1;
