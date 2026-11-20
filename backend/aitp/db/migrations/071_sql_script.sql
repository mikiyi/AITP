-- =====================================================================
-- AITP 迁移 071：数据库脚本资产（SQL 脚本库，供自动化脚本断言/前后置引用）
--   一条 SQL 脚本 = 编号 + 别称 + 正文（可含 {{var}}） + 变量表 + 绑定的执行环境。
--   执行引擎复用 db_checkpoint_service：allowWrite 时放开写操作（前置/后置造数/清理），
--   断言场景走 run_query 只读路径。连接信息不存脚本里，统一由环境页的 DB 连接承载。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_sql_script (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    script_no     TEXT NOT NULL,
    name          TEXT NOT NULL,
    alias         TEXT NOT NULL DEFAULT '',
    content       TEXT NOT NULL DEFAULT '',
    variables     TEXT NOT NULL DEFAULT '',
    env_id        INTEGER REFERENCES biz_test_env(id),
    project_id    INTEGER REFERENCES biz_project(id),
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'ready',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_sql_script_no
    ON biz_sql_script(script_no) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_sql_script_project ON biz_sql_script(project_id);
CREATE INDEX IF NOT EXISTS idx_biz_sql_script_alias ON biz_sql_script(alias);
