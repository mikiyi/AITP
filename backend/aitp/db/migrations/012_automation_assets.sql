-- =====================================================================
-- AITP 迁移 012：自动化脚本资产与执行记录
--   biz_automation_script：web / app / api / performance 四类自动化脚本
--   biz_automation_run   ：每次执行的留痕（命令、退出码、耗时、标准输出/错误）
--   脚本正文存在库里（content），执行时落到数据目录下的独立运行目录，
--   因此不会执行到用户随意指定的路径上。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_automation_script (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    script_no       TEXT NOT NULL,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'web',
    target_type     TEXT,
    framework       TEXT,
    language        TEXT,
    app_platforms   TEXT,
    project_id      INTEGER REFERENCES biz_project(id),
    module_id       INTEGER REFERENCES biz_module(id),
    description     TEXT,
    file_name       TEXT NOT NULL,
    content         TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    run_mode        TEXT NOT NULL DEFAULT 'script',
    entry_command   TEXT,
    timeout_seconds INTEGER,
    status          TEXT NOT NULL DEFAULT 'ready',
    last_run_status TEXT,
    last_run_at     TEXT,
    remark          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_deleted      INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_automation_script_no
    ON biz_automation_script(script_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_automation_script_category
    ON biz_automation_script(category);
CREATE INDEX IF NOT EXISTS idx_biz_automation_script_project
    ON biz_automation_script(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_automation_script_module
    ON biz_automation_script(module_id);

CREATE TABLE IF NOT EXISTS biz_automation_run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_no        TEXT NOT NULL,
    script_id     INTEGER REFERENCES biz_automation_script(id),
    script_no     TEXT,
    script_name   TEXT,
    category      TEXT NOT NULL DEFAULT 'web',
    status        TEXT NOT NULL DEFAULT 'running',
    exit_code     INTEGER,
    duration_ms   INTEGER,
    command_line  TEXT,
    stdout        TEXT,
    stderr        TEXT,
    error_message TEXT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_automation_run_no
    ON biz_automation_run(run_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_automation_run_script
    ON biz_automation_run(script_id);
CREATE INDEX IF NOT EXISTS idx_biz_automation_run_category
    ON biz_automation_run(category);
CREATE INDEX IF NOT EXISTS idx_biz_automation_run_started
    ON biz_automation_run(started_at);

-- 运行时配置默认项：解释器与 JMeter 路径留空，由设置页填写
INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('automation.python.interpreter', '', 'string', 'global',
     '执行 web/app/api 自动化用例的 Python 解释器路径，留空则用当前服务解释器', 1,
     datetime('now'), datetime('now')),
    ('automation.python.timeout', '300', 'int', 'global',
     '自动化用例默认执行超时秒数', 1, datetime('now'), datetime('now')),
    ('performance.jmeter.home', '', 'string', 'global',
     'JMeter 安装根目录（其下有 bin/jmeter 或 bin/jmeter.bat）', 1,
     datetime('now'), datetime('now')),
    ('performance.jmeter.args', '', 'string', 'global',
     'JMeter 启动附加参数，空格分隔，例如 -Jthread.count=50', 1,
     datetime('now'), datetime('now'));
