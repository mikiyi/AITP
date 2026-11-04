-- AI 执行记录：把一次能力执行的结构化结论落库。
-- 证据（引用是否真实存在）、缺项（信息不足处）、风险点、复核结论都是可查、可追的字段，
-- 不再只留下一段模型原文。

CREATE TABLE IF NOT EXISTS biz_ai_record (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          INTEGER REFERENCES biz_ai_task(id),
    session_id       INTEGER REFERENCES biz_ai_session(id),
    capability       TEXT NOT NULL,
    capability_label TEXT,
    model            TEXT,
    summary          TEXT,
    evidence_json    TEXT NOT NULL DEFAULT '[]',
    missing_json     TEXT NOT NULL DEFAULT '[]',
    risk_json        TEXT NOT NULL DEFAULT '[]',
    risk_level       TEXT NOT NULL DEFAULT 'none',
    review_required  INTEGER NOT NULL DEFAULT 0,
    review_status    TEXT NOT NULL DEFAULT 'pending',
    review_note      TEXT,
    reviewed_at      TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    is_deleted       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_ai_record_session ON biz_ai_record(session_id);
CREATE INDEX IF NOT EXISTS idx_biz_ai_record_capability ON biz_ai_record(capability);
CREATE INDEX IF NOT EXISTS idx_biz_ai_record_review ON biz_ai_record(review_status);
CREATE INDEX IF NOT EXISTS idx_biz_ai_record_created ON biz_ai_record(created_at);
