-- =====================================================================
-- AITP 迁移 074：缺陷编号三类前缀（BUG / AI_BUG / Auto_BUG）+ 全局唯一 + 截图
--   1. 缺陷带来源标记 source（manual / ai / automation / import）与 source_ref；
--   2. 旧数据跨项目存在重复编号（各项目都从 BUG-001 起），保留 id 最小的一条，
--      其余顺延到当前最大 BUG 号之后，再建全局唯一索引；
--   3. 新建缺陷截图登记表（文件落 data/bug-screenshots/<bugNo>/）。
-- =====================================================================

ALTER TABLE biz_bug ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE biz_bug ADD COLUMN source_ref TEXT NOT NULL DEFAULT '';

CREATE TEMP TABLE _bug_dup AS
SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS rn
FROM biz_bug b
WHERE b.is_deleted = 0
  AND EXISTS (
      SELECT 1 FROM biz_bug x
      WHERE x.is_deleted = 0 AND x.bug_no = b.bug_no AND x.id < b.id
  );

CREATE TEMP TABLE _bug_base AS
SELECT COALESCE(MAX(CAST(substr(bug_no, 5) AS INTEGER)), 0) AS n
FROM biz_bug
WHERE bug_no GLOB 'BUG-[0-9]*';

UPDATE biz_bug
SET bug_no = 'BUG-' || printf('%03d',
        (SELECT n FROM _bug_base) + (SELECT rn FROM _bug_dup WHERE _bug_dup.id = biz_bug.id))
WHERE id IN (SELECT id FROM _bug_dup);

DROP TABLE _bug_dup;
DROP TABLE _bug_base;

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_bug_no_global
    ON biz_bug(bug_no) WHERE is_deleted = 0;

CREATE TABLE IF NOT EXISTS biz_bug_screenshot (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bug_id        INTEGER NOT NULL,
    seq           INTEGER NOT NULL,
    file_name     TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    mime          TEXT NOT NULL DEFAULT '',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_bug_screenshot_seq
    ON biz_bug_screenshot(bug_id, seq) WHERE is_deleted = 0;
CREATE UNIQUE INDEX IF NOT EXISTS uk_bug_screenshot_file
    ON biz_bug_screenshot(file_name) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_bug_screenshot_bug
    ON biz_bug_screenshot(bug_id);
