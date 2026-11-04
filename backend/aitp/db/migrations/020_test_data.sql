-- =====================================================================
-- AITP 迁移 020：测试数据生成
--   「测试数据生成」的文件分支每次产出一个批次：批次记录生成规则（格式、大小、
--   命名、数量），明细记录每个落盘文件。文件本体在 data/generated/<批次目录>/ 下，
--   库里只存相对路径与实际字节数，方便回看和清理。
--   SQL 分支走 AI 能力与 SQL 分析资产（biz_sql_analysis），不在这里重复建表。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_test_data_batch (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES biz_project(id),
    module_id     INTEGER REFERENCES biz_module(id),
    batch_no      TEXT NOT NULL,
    ext           TEXT NOT NULL,
    format_label  TEXT NOT NULL DEFAULT '',
    build_mode    TEXT NOT NULL DEFAULT '',
    size_bytes    INTEGER,
    file_count    INTEGER NOT NULL DEFAULT 1,
    created_files INTEGER NOT NULL DEFAULT 0,
    total_bytes   INTEGER NOT NULL DEFAULT 0,
    name_prefix   TEXT NOT NULL DEFAULT '',
    name_suffix   TEXT NOT NULL DEFAULT '',
    token_mode    TEXT NOT NULL DEFAULT 'none',
    dir_name      TEXT NOT NULL DEFAULT '',
    remark        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_test_data_batch_no
    ON biz_test_data_batch(batch_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_test_data_batch_project ON biz_test_data_batch(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_test_data_batch_ext ON biz_test_data_batch(ext);

CREATE TABLE IF NOT EXISTS biz_test_data_file (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES biz_test_data_batch(id) ON DELETE CASCADE,
    file_name   TEXT NOT NULL,
    ext         TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    exact       INTEGER NOT NULL DEFAULT 1,
    note        TEXT NOT NULL DEFAULT '',
    rel_path    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_test_data_file_batch ON biz_test_data_file(batch_id);
