-- =====================================================================
-- AITP 迁移 076：AI 修复建议（四期专项 A）
--   接口执行断言失败后生成的「资产修改建议」，编号 AI_FIX-001 起全局自增，
--   软删不复用（与 BUG-/AI_BUG-/Auto_BUG 同构口径）。
--   建议 AI 只建议、人决策：状态 pending/applied/dismissed，采纳走人工确认，
--   终态锁定不可二次采纳。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_fix_suggestion (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_no    TEXT NOT NULL,
    project_id      INTEGER NOT NULL,
    execution_id    INTEGER NOT NULL,
    evidence_id     INTEGER,
    case_id         INTEGER NOT NULL,
    case_no         TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT 'ai',
    target_type     TEXT NOT NULL DEFAULT 'assertion',
    suggestion_json TEXT NOT NULL DEFAULT '{}',
    diff_preview    TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    model           TEXT NOT NULL DEFAULT '',
    usage_json      TEXT NOT NULL DEFAULT '{}',
    cost_json       TEXT NOT NULL DEFAULT '{}',
    degrade_json    TEXT NOT NULL DEFAULT '{}',
    applied_at      TEXT NOT NULL DEFAULT '',
    applied_by      TEXT NOT NULL DEFAULT '',
    dismissed_at    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_fix_suggestion_no
    ON biz_fix_suggestion(suggestion_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_fix_suggestion_project
    ON biz_fix_suggestion(project_id, status);
CREATE INDEX IF NOT EXISTS idx_fix_suggestion_execution
    ON biz_fix_suggestion(execution_id);
