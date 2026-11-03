-- =====================================================================
-- AITP 迁移 001：AI 与系统配置核心表
-- =====================================================================

CREATE TABLE IF NOT EXISTS sys_schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT    NOT NULL,
    checksum    TEXT,
    app_version TEXT
);

CREATE TABLE IF NOT EXISTS sys_config (
    config_key   TEXT PRIMARY KEY,
    config_value TEXT,
    value_type   TEXT NOT NULL DEFAULT 'string',
    scope        TEXT NOT NULL DEFAULT 'global',
    description  TEXT,
    is_system    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sys_ai_profile (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    provider      TEXT NOT NULL,
    protocol      TEXT NOT NULL DEFAULT 'openai_compat',
    base_url      TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    api_key_enc   TEXT,
    api_key_masked TEXT,
    extra_json    TEXT,
    temperature   REAL NOT NULL DEFAULT 0.7,
    max_tokens    INTEGER NOT NULL DEFAULT 4096,
    timeout       INTEGER NOT NULL DEFAULT 60,
    is_default    INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_sys_ai_profile_name ON sys_ai_profile(name);
CREATE INDEX IF NOT EXISTS idx_sys_ai_profile_provider ON sys_ai_profile(provider, protocol);
CREATE INDEX IF NOT EXISTS idx_sys_ai_profile_active ON sys_ai_profile(is_active, is_default);

CREATE TABLE IF NOT EXISTS sys_ai_prompt (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    file_path      TEXT NOT NULL,
    prompt_version TEXT,
    variables_json TEXT,
    description    TEXT,
    checksum       TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    is_deleted     INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_sys_ai_prompt_code ON sys_ai_prompt(code);
CREATE INDEX IF NOT EXISTS idx_sys_ai_prompt_active ON sys_ai_prompt(is_active);
