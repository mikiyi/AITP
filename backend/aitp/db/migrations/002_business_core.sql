-- =====================================================================
-- AITP 迁移 002：业务表 + FTS5 中文全文索引
-- 建表顺序遵守外键依赖：project -> session -> task -> case/bug -> 关联表
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_project (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    owner       TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_project_code ON biz_project(code) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_project_status ON biz_project(status);

CREATE TABLE IF NOT EXISTS biz_ai_session (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL DEFAULT '新会话',
    module     TEXT NOT NULL DEFAULT 'general',
    biz_type   TEXT,
    biz_id     INTEGER,
    profile_id INTEGER REFERENCES sys_ai_profile(id),
    model      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_ai_session_module ON biz_ai_session(module);
CREATE INDEX IF NOT EXISTS idx_biz_ai_session_created ON biz_ai_session(created_at);

CREATE TABLE IF NOT EXISTS biz_ai_task (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type     TEXT NOT NULL,
    biz_type      TEXT,
    biz_id        INTEGER,
    session_id    INTEGER REFERENCES biz_ai_session(id),
    profile_id    INTEGER REFERENCES sys_ai_profile(id),
    status        TEXT NOT NULL DEFAULT 'pending',
    progress      INTEGER NOT NULL DEFAULT 0,
    request_json  TEXT,
    result_json   TEXT,
    error_code    INTEGER,
    error_message TEXT,
    started_at    TEXT,
    finished_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_ai_task_status ON biz_ai_task(status);
CREATE INDEX IF NOT EXISTS idx_biz_ai_task_type ON biz_ai_task(task_type);
CREATE INDEX IF NOT EXISTS idx_biz_ai_task_session ON biz_ai_task(session_id);

CREATE TABLE IF NOT EXISTS biz_ai_message (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        INTEGER NOT NULL REFERENCES biz_ai_session(id),
    role              TEXT NOT NULL,
    content           TEXT NOT NULL,
    reasoning         TEXT,
    prompt_code       TEXT,
    model             TEXT,
    tokens_prompt     INTEGER,
    tokens_completion INTEGER,
    finish_reason     TEXT,
    error_code        INTEGER,
    message_meta      TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    is_deleted        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_ai_message_session ON biz_ai_message(session_id);
CREATE INDEX IF NOT EXISTS idx_biz_ai_message_created ON biz_ai_message(created_at);

CREATE TABLE IF NOT EXISTS biz_requirement (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES biz_project(id),
    req_no      TEXT,
    title       TEXT NOT NULL,
    content     TEXT,
    source      TEXT,
    version     TEXT,
    status      TEXT NOT NULL DEFAULT 'draft',
    priority    TEXT NOT NULL DEFAULT 'P2',
    tags        TEXT,
    ai_task_id  INTEGER REFERENCES biz_ai_task(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_requirement_project ON biz_requirement(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_requirement_no ON biz_requirement(req_no);
CREATE INDEX IF NOT EXISTS idx_biz_requirement_status ON biz_requirement(status);

CREATE TABLE IF NOT EXISTS biz_test_case (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES biz_project(id),
    requirement_id  INTEGER REFERENCES biz_requirement(id),
    case_no         TEXT NOT NULL,
    title           TEXT NOT NULL,
    case_type       TEXT NOT NULL DEFAULT 'functional',
    priority        TEXT NOT NULL DEFAULT 'P2',
    status          TEXT NOT NULL DEFAULT 'draft',
    preconditions   TEXT,
    steps_json      TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT,
    actual_result   TEXT,
    tags            TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    ai_task_id      INTEGER REFERENCES biz_ai_task(id),
    creator         TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_test_case_no ON biz_test_case(project_id, case_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_test_case_project ON biz_test_case(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_test_case_requirement ON biz_test_case(requirement_id);
CREATE INDEX IF NOT EXISTS idx_biz_test_case_type ON biz_test_case(case_type);
CREATE INDEX IF NOT EXISTS idx_biz_test_case_priority ON biz_test_case(priority);
CREATE INDEX IF NOT EXISTS idx_biz_test_case_status ON biz_test_case(status);

CREATE TABLE IF NOT EXISTS biz_bug (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES biz_project(id),
    bug_no          TEXT NOT NULL,
    title           TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'P2',
    status          TEXT NOT NULL DEFAULT 'open',
    priority        TEXT NOT NULL DEFAULT 'P2',
    bug_type        TEXT NOT NULL DEFAULT 'functional',
    description     TEXT,
    steps_json      TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT,
    actual_result   TEXT,
    assignee        TEXT,
    reporter        TEXT,
    version         TEXT,
    env             TEXT,
    module          TEXT,
    ai_task_id      INTEGER REFERENCES biz_ai_task(id),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_bug_no ON biz_bug(project_id, bug_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_bug_project ON biz_bug(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_bug_status ON biz_bug(status);
CREATE INDEX IF NOT EXISTS idx_biz_bug_severity ON biz_bug(severity);
CREATE INDEX IF NOT EXISTS idx_biz_bug_assignee ON biz_bug(assignee);

CREATE TABLE IF NOT EXISTS biz_case_bug_rel (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       INTEGER NOT NULL REFERENCES biz_test_case(id),
    bug_id        INTEGER NOT NULL REFERENCES biz_bug(id),
    relation_type TEXT NOT NULL DEFAULT 'related',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_case_bug_rel ON biz_case_bug_rel(case_id, bug_id);
CREATE INDEX IF NOT EXISTS idx_biz_case_bug_rel_bug ON biz_case_bug_rel(bug_id);

CREATE TABLE IF NOT EXISTS biz_case_execution (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id      INTEGER NOT NULL REFERENCES biz_test_case(id),
    bug_id       INTEGER REFERENCES biz_bug(id),
    result       TEXT NOT NULL,
    duration_ms  INTEGER,
    environment  TEXT,
    version      TEXT,
    executed_by  TEXT,
    executed_at  TEXT NOT NULL,
    remark       TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    is_deleted   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_case_execution_case ON biz_case_execution(case_id);
CREATE INDEX IF NOT EXISTS idx_biz_case_execution_result ON biz_case_execution(result);
CREATE INDEX IF NOT EXISTS idx_biz_case_execution_at ON biz_case_execution(executed_at);

CREATE TABLE IF NOT EXISTS biz_bug_status_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bug_id      INTEGER NOT NULL REFERENCES biz_bug(id),
    from_status TEXT,
    to_status   TEXT NOT NULL,
    operator    TEXT,
    comment     TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_biz_bug_status_log_bug ON biz_bug_status_log(bug_id);
CREATE INDEX IF NOT EXISTS idx_biz_bug_status_log_created ON biz_bug_status_log(created_at);

CREATE TABLE IF NOT EXISTS biz_knowledge_article (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    summary     TEXT,
    category    TEXT NOT NULL DEFAULT 'general',
    tags        TEXT,
    source_type TEXT NOT NULL DEFAULT 'manual',
    session_id  INTEGER REFERENCES biz_ai_session(id),
    ai_task_id  INTEGER REFERENCES biz_ai_task(id),
    project_id  INTEGER REFERENCES biz_project(id),
    ref_type    TEXT,
    ref_id      INTEGER,
    view_count  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'published',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_knowledge_article_category ON biz_knowledge_article(category);
CREATE INDEX IF NOT EXISTS idx_biz_knowledge_article_source ON biz_knowledge_article(source_type);
CREATE INDEX IF NOT EXISTS idx_biz_knowledge_article_session ON biz_knowledge_article(session_id);
CREATE INDEX IF NOT EXISTS idx_biz_knowledge_article_created ON biz_knowledge_article(created_at);

-- 中文全文索引：trigram 分词器支持中文子串匹配；unicode61 对中文无效
CREATE VIRTUAL TABLE IF NOT EXISTS biz_knowledge_fts USING fts5(
    title,
    content,
    tags,
    content='biz_knowledge_article',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS trg_knowledge_fts_ai AFTER INSERT ON biz_knowledge_article BEGIN
    INSERT INTO biz_knowledge_fts(rowid, title, content, tags)
    VALUES (new.id, new.title, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS trg_knowledge_fts_ad AFTER DELETE ON biz_knowledge_article BEGIN
    INSERT INTO biz_knowledge_fts(biz_knowledge_fts, rowid, title, content, tags)
    VALUES ('delete', old.id, old.title, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS trg_knowledge_fts_au AFTER UPDATE ON biz_knowledge_article BEGIN
    INSERT INTO biz_knowledge_fts(biz_knowledge_fts, rowid, title, content, tags)
    VALUES ('delete', old.id, old.title, old.content, old.tags);
    INSERT INTO biz_knowledge_fts(rowid, title, content, tags)
    VALUES (new.id, new.title, new.content, new.tags);
END;

CREATE TABLE IF NOT EXISTS biz_report (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES biz_project(id),
    report_no    TEXT,
    title        TEXT NOT NULL,
    report_type  TEXT NOT NULL DEFAULT 'summary',
    period_start TEXT,
    period_end   TEXT,
    summary      TEXT,
    content      TEXT,
    status       TEXT NOT NULL DEFAULT 'draft',
    ai_task_id   INTEGER REFERENCES biz_ai_task(id),
    creator      TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    is_deleted   INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_report_no ON biz_report(project_id, report_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_report_status ON biz_report(status);
CREATE INDEX IF NOT EXISTS idx_biz_report_created ON biz_report(created_at);

CREATE TABLE IF NOT EXISTS biz_report_item (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id  INTEGER NOT NULL REFERENCES biz_report(id),
    section    TEXT NOT NULL,
    title      TEXT,
    content    TEXT,
    metric_json TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_report_item_report ON biz_report_item(report_id);
CREATE INDEX IF NOT EXISTS idx_biz_report_item_section ON biz_report_item(section);

CREATE TABLE IF NOT EXISTS biz_report_template (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    code         TEXT NOT NULL,
    description  TEXT,
    sections_json TEXT,
    is_default   INTEGER NOT NULL DEFAULT 0,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    is_deleted   INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_report_template_code ON biz_report_template(code);
CREATE INDEX IF NOT EXISTS idx_biz_report_template_active ON biz_report_template(is_active);
