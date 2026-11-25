-- =====================================================================
-- AITP 迁移 087：V2.7.0 平台能力
--   1) 通知模板：渠道类型（webhook/dingtalk/wecom/feishu/email）、标题/正文模板、
--      渠道私有配置（邮件 SMTP 等；密码只进系统密钥库，config_json 仅存引用与掩码）。
--   2) 缺陷待审：自动化失败先生成 biz_bug_draft，人确认后才落 biz_bug（半自动流转）。
--   3) 用例依赖：biz_case_dependency 支撑变更影响面分析（禁自依赖/禁环由服务层校验）。
--   4) MCP 调用留痕：sys_mcp_call_log 记录外部 AI 客户端的工具调用，便于审计与排查。
-- =====================================================================

ALTER TABLE sys_notification_channel ADD COLUMN channel_type TEXT NOT NULL DEFAULT 'webhook';
ALTER TABLE sys_notification_channel ADD COLUMN template_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sys_notification_channel ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS biz_bug_draft (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES biz_project(id),
    source         TEXT NOT NULL DEFAULT 'automation',
    source_ref     TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    actual_result  TEXT NOT NULL DEFAULT '',
    severity       TEXT NOT NULL DEFAULT 'P2',
    priority       TEXT NOT NULL DEFAULT 'P2',
    bug_type       TEXT NOT NULL DEFAULT 'functional',
    reporter       TEXT NOT NULL DEFAULT '',
    case_ids_json  TEXT NOT NULL DEFAULT '[]',
    status         TEXT NOT NULL DEFAULT 'pending',
    bug_id         INTEGER REFERENCES biz_bug(id),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    is_deleted     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_bug_draft_project ON biz_bug_draft(project_id, status);
CREATE INDEX IF NOT EXISTS idx_biz_bug_draft_source ON biz_bug_draft(source, source_ref);

CREATE TABLE IF NOT EXISTS biz_case_dependency (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id          INTEGER NOT NULL REFERENCES biz_project(id),
    case_id             INTEGER NOT NULL REFERENCES biz_test_case(id),
    depends_on_case_id  INTEGER NOT NULL REFERENCES biz_test_case(id),
    note                TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_case_dependency
    ON biz_case_dependency(case_id, depends_on_case_id);
CREATE INDEX IF NOT EXISTS idx_biz_case_dependency_project
    ON biz_case_dependency(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_case_dependency_target
    ON biz_case_dependency(depends_on_case_id);

CREATE TABLE IF NOT EXISTS sys_mcp_call_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    method      TEXT NOT NULL DEFAULT '',
    tool        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'ok',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT '',
    client      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sys_mcp_call_log_created ON sys_mcp_call_log(created_at);

INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('automation.auto_bug_mode', 'review', 'string', 'global',
     '自动化失败转缺陷模式：off=关闭 / review=生成待审草稿 / auto=直接建缺陷', 1,
     datetime('now'), datetime('now')),
    ('app.adb_command', 'adb', 'string', 'global',
     'App 场景执行的 adb 命令（可指向自定义可执行文件）', 1,
     datetime('now'), datetime('now')),
    ('app.device_serial', '', 'string', 'global',
     'App 场景默认设备序列号，留空则使用唯一在线设备', 1,
     datetime('now'), datetime('now')),
    ('app.command_timeout', '20', 'int', 'global',
     'App 场景单条 adb 命令超时秒数', 1,
     datetime('now'), datetime('now')),
    ('mcp.enabled', '0', 'bool', 'global', '是否开放 AITP MCP 服务端', 1,
     datetime('now'), datetime('now')),
    ('mcp.token', '', 'string', 'global', 'MCP 访问令牌，留空表示仅回环免令牌', 1,
     datetime('now'), datetime('now')),
    ('mcp.allow_write', '0', 'bool', 'global',
     'MCP 是否允许写操作（写操作只落待审区）', 1,
     datetime('now'), datetime('now'));
