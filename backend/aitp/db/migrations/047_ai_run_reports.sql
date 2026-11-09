-- =====================================================================
-- AITP 迁移 047：AI 实验室「装配 / 校验 / 去重 / 成本」四份结论落库
--   GAP-27 上下文装配器：本次注入的项目内事实（档位、条目、裁剪情况）
--   GAP-28 输出 schema 校验：通过/拦下条数、原因、是否自动修正
--   GAP-29 生成去重：比较域、疑似重复清单、增量模式摘走的条目
--   GAP-30 成本：档案级 token 单价与本次折算金额
--
--   口径与 032（脱敏留痕）一致：只存**结论**，不存上下文原文之外的东西；
--   上下文块正文本来就是库里已有的标题/摘要片段，随记录留一份便于事后解释
--   「当时模型看到了什么」，因此 context_json 存完整结论（含块列表，已截断）。
--
--   金额一律用「微元」整数（1 元 = 1_000_000 微元），避免浮点误差与四舍五入
--   口径争议；单价为「每 1000 token 的微元数」，0 表示未配价（只报 token 不报钱）。
-- =====================================================================

ALTER TABLE biz_ai_record ADD COLUMN schema_json   TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_ai_record ADD COLUMN context_json  TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_ai_record ADD COLUMN dedup_json    TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_ai_record ADD COLUMN cost_micros   INTEGER;
ALTER TABLE biz_ai_record ADD COLUMN cost_currency TEXT NOT NULL DEFAULT '';

ALTER TABLE sys_ai_profile ADD COLUMN price_input_micros  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sys_ai_profile ADD COLUMN price_output_micros INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sys_ai_profile ADD COLUMN price_currency      TEXT NOT NULL DEFAULT 'CNY';

INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('ai.context.tier', 'standard', 'string', 'global',
     'AI 调用前装配项目内上下文的档位：lite=精简 / standard=标准 / full=完整', 1,
     datetime('now'), datetime('now')),
    ('ai.dedup.mode', 'mark', 'string', 'global',
     '生成结果的去重口径：mark=只标记疑似重复（可恢复）/ increment=按已有基线摘走确定重复', 1,
     datetime('now'), datetime('now')),
    ('ai.budget.daily_micros', '0', 'int', 'global',
     '每日 AI 花费软阈值（微元，1 元 = 1000000）；0 表示不设限，超了只提醒不拦截', 1,
     datetime('now'), datetime('now')),
    ('ai.budget.monthly_micros', '0', 'int', 'global',
     '每月 AI 花费软阈值（微元）；0 表示不设限，超了只提醒不拦截', 1,
     datetime('now'), datetime('now')),
    ('ai.budget.warn_ratio', '80', 'int', 'global',
     '花费达到软阈值的百分之几时开始提示（1~100）', 1,
     datetime('now'), datetime('now'));
