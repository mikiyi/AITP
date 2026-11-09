-- =====================================================================
-- AITP 迁移 049：主线 B（B1 GAP-32 + B2 GAP-33/34/35/36）执行证据底座
--   号位说明：二期清单 5.5 的分配表把 047 给 C2、048 给 A1、049 给 B1、050 给 B2；
--   本批按开工口径「只用 049」把 B 线两批的结构变更合并进同一个顺序号脚本，
--   不再占用 050，避免与并行批次撞号。分四段：
--     1) biz_execution_evidence —— 一次执行一份证据快照（GAP-32）
--     2) biz_regression_item 四列 —— 回归条目标执行方式与真实执行回写（GAP-33）
--     3) biz_assertion_rule —— 可复用断言库，按项目/功能点共享（GAP-34）
--     4) biz_requirement.module_id —— 需求直挂功能点（GAP-36）
--   回填口径：四段全部是「新增可为空 / 带安全默认值」，不改任何既有行语义。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1) 执行证据快照（GAP-32）
--   三条执行路径（接口 / 自动化 / 手工）与回归条目执行都往这里落一份统一结构，
--   大字段只存摘要并靠 source_type + source_row_id 指回原表，不把整段响应长期存这。
--   一次执行一份：同 source_type + source_row_id 重复写入时覆盖（见执行服务的 capture）。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS biz_execution_evidence (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type        TEXT NOT NULL,                -- interface / automation / manual / regression
    source_row_id      INTEGER NOT NULL DEFAULT 0,   -- 原表行 id
    project_id         INTEGER REFERENCES biz_project(id),
    case_id            INTEGER REFERENCES biz_test_case(id),
    run_id             INTEGER REFERENCES biz_test_run(id),
    run_case_id        INTEGER REFERENCES biz_test_run_case(id),
    regression_id      INTEGER REFERENCES biz_regression(id),
    regression_item_id INTEGER REFERENCES biz_regression_item(id),
    bug_id             INTEGER REFERENCES biz_bug(id),
    env_id             INTEGER REFERENCES biz_test_env(id),
    env_name           TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    result             TEXT NOT NULL DEFAULT 'not_executed',
    duration_ms        INTEGER NOT NULL DEFAULT 0,
    assertions_json    TEXT NOT NULL DEFAULT '[]',   -- 统一断言明细：name/passed/expected/actual/message
    request_summary    TEXT NOT NULL DEFAULT '',     -- 请求摘要（方法 + URL + 头 + 体，截断）
    response_summary   TEXT NOT NULL DEFAULT '',     -- 响应摘要（状态 + 体片段，截断）
    log_summary        TEXT NOT NULL DEFAULT '',     -- 日志 / stdout / stderr 摘要
    attachments_json   TEXT NOT NULL DEFAULT '[]',   -- 附件引用列表（复用 lab 附件体系）
    source_ref_json    TEXT NOT NULL DEFAULT '{}',   -- 指回原表的定位信息
    trace_id           TEXT NOT NULL DEFAULT '',     -- GAP-23 的当次 traceId
    error_message      TEXT NOT NULL DEFAULT '',
    executed_by        TEXT NOT NULL DEFAULT '',
    executed_at        TEXT,
    expires_at         TEXT,                         -- 保留到期时间；为空表示随轮次永久保留
    purge_reason       TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    is_deleted         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_exec_evidence_source
    ON biz_execution_evidence(source_type, source_row_id);
CREATE INDEX IF NOT EXISTS idx_biz_exec_evidence_run_case
    ON biz_execution_evidence(run_case_id);
CREATE INDEX IF NOT EXISTS idx_biz_exec_evidence_case
    ON biz_execution_evidence(case_id);
CREATE INDEX IF NOT EXISTS idx_biz_exec_evidence_run
    ON biz_execution_evidence(run_id);
CREATE INDEX IF NOT EXISTS idx_biz_exec_evidence_bug
    ON biz_execution_evidence(bug_id);
CREATE INDEX IF NOT EXISTS idx_biz_exec_evidence_item
    ON biz_execution_evidence(regression_item_id);
CREATE INDEX IF NOT EXISTS idx_biz_exec_evidence_expires
    ON biz_execution_evidence(expires_at);

-- ---------------------------------------------------------------------
-- 2) 回归条目：执行方式与真实执行回写（GAP-33）
--   exec_mode：auto=可自动（必须关联一条接口用例，由接口执行器真跑）、
--              manual=手工（允许批量回写，但标记通过必须有证据，见清单决策 4）。
--   新增列默认 manual：存量条目从未真跑过、也没有用例引用，按手工口径处理最安全
--   （不猜、不自动跑），由用户在回归清单页显式改标 auto 并关联用例。
--   case_id / env_id：auto 条目指向被测用例与执行环境，可空。
--   evidence_id：最近一次真跑（或手工上传）的证据快照 id，可空=从未留证。
-- ---------------------------------------------------------------------
ALTER TABLE biz_regression_item ADD COLUMN exec_mode TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE biz_regression_item ADD COLUMN case_id INTEGER REFERENCES biz_test_case(id);
ALTER TABLE biz_regression_item ADD COLUMN env_id INTEGER REFERENCES biz_test_env(id);
ALTER TABLE biz_regression_item ADD COLUMN evidence_id INTEGER REFERENCES biz_execution_evidence(id);

CREATE INDEX IF NOT EXISTS idx_biz_regression_item_mode ON biz_regression_item(exec_mode);
CREATE INDEX IF NOT EXISTS idx_biz_regression_item_case ON biz_regression_item(case_id);

-- ---------------------------------------------------------------------
-- 3) 断言库（GAP-34）
--   把「目标 + 算子 + 期望」抽成可复用条目，按项目共享（module_id 为空）
--   或按功能点共享（module_id 指向该节点，接口用例引用时写 lib:<rule_no>）。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS biz_assertion_rule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES biz_project(id),
    module_id   INTEGER REFERENCES biz_module(id),
    rule_no     TEXT NOT NULL,
    name        TEXT NOT NULL,
    rule        TEXT NOT NULL,                    -- 与用例「断言规则」同语法：json:$.code = 0
    description TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_assertion_rule_no
    ON biz_assertion_rule(project_id, rule_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_assertion_rule_module ON biz_assertion_rule(module_id);

-- ---------------------------------------------------------------------
-- 4) 需求直挂功能点（GAP-36）
--   biz_requirement 一直没有 module_id，追溯只能到需求层。
--   回填口径：全部留空（NULL）。一期数据没有任何「需求↔功能点」的可靠来源
--   （biz_entity_relation 里也没有存过 requirement→module 的关系），
--   凭空回填会把追溯矩阵算错，因此明确不回填，由用户在需求页逐条挂接；
--   清单第 3 节也写明「回填可为空，不阻塞」。
-- ---------------------------------------------------------------------
ALTER TABLE biz_requirement ADD COLUMN module_id INTEGER REFERENCES biz_module(id);

CREATE INDEX IF NOT EXISTS idx_biz_requirement_module ON biz_requirement(module_id);

-- ---------------------------------------------------------------------
-- 证据保留策略配置（GAP-32 功能点 4）
--   全局默认 180 天；项目级覆盖键 execution.evidence_retention_days:<项目 id>，
--   值 0 表示「随轮次永久保留」（expires_at 写空，清理任务不动它）。
-- ---------------------------------------------------------------------
INSERT INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
SELECT 'execution.evidence_retention_days', '180', 'int', 'global',
       '执行证据快照默认保留天数；0=随轮次永久保留。项目级覆盖键 '
       'execution.evidence_retention_days:<项目id>。',
       1, datetime('now'), datetime('now')
WHERE NOT EXISTS (
    SELECT 1 FROM sys_config WHERE config_key = 'execution.evidence_retention_days'
);
