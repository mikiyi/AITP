-- =====================================================================
-- AITP 迁移 009：回归清单实体 + 通用实体关联
--   biz_regression / biz_regression_item：回归清单可落库、逐条在线执行
--   biz_entity_relation：任意实体之间的通用关联（用例/缺陷/需求/知识/回归）
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_regression (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES biz_project(id),
    title            TEXT NOT NULL,
    change_scope     TEXT,
    affected_modules TEXT,
    version          TEXT,
    risk_level       TEXT NOT NULL DEFAULT 'low',
    status           TEXT NOT NULL DEFAULT 'draft',
    source           TEXT NOT NULL DEFAULT 'manual',
    ai_task_id       INTEGER REFERENCES biz_ai_task(id),
    content          TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    is_deleted       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_regression_project ON biz_regression(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_regression_status ON biz_regression(status);
CREATE INDEX IF NOT EXISTS idx_biz_regression_risk ON biz_regression(risk_level);

CREATE TABLE IF NOT EXISTS biz_regression_item (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    regression_id INTEGER NOT NULL REFERENCES biz_regression(id),
    seq           INTEGER NOT NULL DEFAULT 0,
    title         TEXT NOT NULL,
    module        TEXT,
    priority      TEXT NOT NULL DEFAULT 'P1',
    reason        TEXT,
    verify_target TEXT,
    result        TEXT NOT NULL DEFAULT 'not_executed',
    remark        TEXT,
    executed_by   TEXT,
    executed_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_regression_item_reg ON biz_regression_item(regression_id);
CREATE INDEX IF NOT EXISTS idx_biz_regression_item_result ON biz_regression_item(result);

-- 通用实体关联：一对多关系统一存这里，source/target 用「类型:id」定位
CREATE TABLE IF NOT EXISTS biz_entity_relation (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type   TEXT NOT NULL,
    source_id     INTEGER NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'related',
    target_type   TEXT NOT NULL,
    target_id     INTEGER NOT NULL,
    remark        TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_entity_relation
    ON biz_entity_relation(source_type, source_id, target_type, target_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_biz_entity_relation_source ON biz_entity_relation(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_biz_entity_relation_target ON biz_entity_relation(target_type, target_id);
