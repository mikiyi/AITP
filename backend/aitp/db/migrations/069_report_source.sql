-- =====================================================================
-- AITP 迁移 069：报告按轮次 / 矩阵 / 场景批次取数（P1-8）
--   报告记录生成来源，详情、统计与失败样本都按来源收敛；
--   source_type: run=测试轮次 / matrix=执行矩阵 / scenario=接口场景批次。
-- =====================================================================

ALTER TABLE biz_report ADD COLUMN source_type TEXT NOT NULL DEFAULT '';
ALTER TABLE biz_report ADD COLUMN source_id INTEGER;
ALTER TABLE biz_report ADD COLUMN source_run_id INTEGER;
ALTER TABLE biz_report ADD COLUMN source_label TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_biz_report_source
    ON biz_report(source_type, source_id) WHERE source_type <> '';
