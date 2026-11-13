-- =====================================================================
-- AITP 迁移 056：Web 探索性测试（GAP-45）
--   biz_exploration_session  一次探索会话：目标地址、驱动方式、状态、收敛统计
--   biz_exploration_step     会话内每一步：读快照→决策→动作→观测，异常步挂证据与待审产物
--   sys_config               exploratory.web.settings 一份 JSON：步数/时长/脱敏/截图/日志切片/MCP
--   产物只进待审池（biz_knowledge_draft source_type='exploratory'），人工审核通过才转缺陷；
--   证据走统一执行证据（biz_execution_evidence source_type='exploratory'）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_exploration_session (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_no    TEXT NOT NULL,
    project_id    INTEGER NOT NULL REFERENCES biz_project(id),
    name          TEXT NOT NULL DEFAULT '',
    target_url    TEXT NOT NULL DEFAULT '',
    driver        TEXT NOT NULL DEFAULT 'scripted',   -- scripted / model
    status        TEXT NOT NULL DEFAULT 'draft',      -- draft/queued/running/done/failed/cancelled
    task_id       INTEGER,
    max_steps     INTEGER NOT NULL DEFAULT 20,
    max_seconds   INTEGER NOT NULL DEFAULT 180,
    settings_json TEXT NOT NULL DEFAULT '{}',         -- 发起时的配置快照，可复盘
    plan_json     TEXT NOT NULL DEFAULT '[]',         -- scripted 驱动的确定性探索计划
    step_total    INTEGER NOT NULL DEFAULT 0,
    step_done     INTEGER NOT NULL DEFAULT 0,
    anomaly_count INTEGER NOT NULL DEFAULT 0,
    draft_count   INTEGER NOT NULL DEFAULT 0,
    degraded      INTEGER NOT NULL DEFAULT 0,         -- model 驱动无可用模型时降级为 scripted
    summary_json  TEXT NOT NULL DEFAULT '{}',
    error_message TEXT NOT NULL DEFAULT '',
    started_at    TEXT,
    finished_at   TEXT,
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_exploration_session_no
    ON biz_exploration_session(session_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_exploration_session_project
    ON biz_exploration_session(project_id, created_at) WHERE is_deleted = 0;

CREATE TABLE IF NOT EXISTS biz_exploration_step (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES biz_exploration_session(id),
    step_no        INTEGER NOT NULL DEFAULT 0,
    action         TEXT NOT NULL DEFAULT '',          -- navigate/click/type/snapshot/screenshot/observe
    selector       TEXT NOT NULL DEFAULT '',          -- 可靠元素定位（recordSelectors 开启时留）
    page_url       TEXT NOT NULL DEFAULT '',
    input_masked   TEXT NOT NULL DEFAULT '',          -- 脱敏后的输入值，绝不留明文口令
    snapshot_summary TEXT NOT NULL DEFAULT '',
    observation    TEXT NOT NULL DEFAULT '',
    is_anomaly     INTEGER NOT NULL DEFAULT 0,
    anomaly_type   TEXT NOT NULL DEFAULT '',          -- page_error/http_error/console_error/business_code
    detected_at    TEXT NOT NULL DEFAULT '',          -- 异常锚点
    last_ok_at     TEXT NOT NULL DEFAULT '',          -- 最近一次正常锚点（双锚点切片用）
    evidence_id    INTEGER,                           -- biz_execution_evidence.id
    draft_id       INTEGER,                           -- biz_knowledge_draft.id
    trace_id       TEXT NOT NULL DEFAULT '',
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    detail_json    TEXT NOT NULL DEFAULT '{}',        -- 截图/控制台/网络/日志切片引用
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    is_deleted     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_exploration_step_session
    ON biz_exploration_step(session_id, step_no) WHERE is_deleted = 0;

-- 探索性产物审核通过后转缺陷：待审池登记转出的缺陷 id（其余来源仍走 article_id）
ALTER TABLE biz_knowledge_draft ADD COLUMN bug_id INTEGER REFERENCES biz_bug(id);

-- ---------------------------------------------------------------------
-- 探索性测试默认配置：一份 JSON，settings 读写都走它
-- ---------------------------------------------------------------------
INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('exploratory.web.settings',
     '{"maxSteps":20,"maxSeconds":180,"perStepTimeout":30,"perStepScreenshots":1,"recordSelectors":true,"inputMasking":true,"screenshotDir":"exploratory/screenshots","logSliceDir":"exploratory/log-slices","logSources":[],"logWindowSeconds":5,"mcpCommand":"","pinnedTools":["browser_navigate","browser_click","browser_type","browser_snapshot","browser_screenshot","browser_console_messages","browser_network_requests"]}',
     'json', 'global',
     'Web 探索性测试配置：步数/时长/脱敏/截图/日志切片窗口与来源/MCP 命令与钉住工具；截图与日志目录相对 data 目录', 1,
     datetime('now'), datetime('now'));