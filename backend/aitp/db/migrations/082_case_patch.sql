-- =====================================================================
-- AITP 迁移 082：用例局部 Diff 修订（V2.6.0）
--   一次自然语言指令（补异常场景 / 删重复 / 重编号）产出一批待审 patch，
--   每条 patch 独立采纳或忽略；采纳前不改用例，采纳后逐条回写并留审计。
--   编号 AI_PATCH-001 起全局自增，软删不复用。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_case_patch (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patch_no      TEXT NOT NULL,
    project_id    INTEGER NOT NULL,
    instruction   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    model         TEXT NOT NULL DEFAULT '',
    usage_json    TEXT NOT NULL DEFAULT '{}',
    cost_json     TEXT NOT NULL DEFAULT '{}',
    degrade_json  TEXT NOT NULL DEFAULT '{}',
    created_by    TEXT NOT NULL DEFAULT 'local',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_case_patch_no
    ON biz_case_patch(patch_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_case_patch_project
    ON biz_case_patch(project_id, status);

CREATE TABLE IF NOT EXISTS biz_case_patch_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patch_id    INTEGER NOT NULL REFERENCES biz_case_patch(id),
    project_id  INTEGER NOT NULL,
    case_id     INTEGER NOT NULL,
    case_no     TEXT NOT NULL DEFAULT '',
    case_title  TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL DEFAULT 'update',
    target      TEXT NOT NULL DEFAULT 'steps',
    before_text TEXT NOT NULL DEFAULT '',
    after_text  TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    applied_at  TEXT NOT NULL DEFAULT '',
    applied_by  TEXT NOT NULL DEFAULT '',
    dismissed_at TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_case_patch_item_patch
    ON biz_case_patch_item(patch_id, status);
CREATE INDEX IF NOT EXISTS idx_biz_case_patch_item_case
    ON biz_case_patch_item(case_id);
