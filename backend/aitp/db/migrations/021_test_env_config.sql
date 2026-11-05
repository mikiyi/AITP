-- =====================================================================
-- AITP 迁移 021：测试环境配置
--   一行 = 一套命名环境（开发 / 测试 / 预发…），含应用服务器与数据库服务器
--   两组目标信息。数据库口令本体进 OS 密钥库（test_env:<id>），库内只存
--   掩码与引用，绝不回显明文；整套配置只用于本机的环境健康监测，
--   不参与任何大模型调用。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_test_env (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    -- 应用服务器：健康监测用 HTTP 探测
    app_base_url     TEXT NOT NULL DEFAULT '',
    app_health_path  TEXT NOT NULL DEFAULT '',
    -- 数据库服务器：健康监测用 TCP 端口探测 + 可选登录验证
    db_type          TEXT NOT NULL DEFAULT 'mysql',
    db_host          TEXT NOT NULL DEFAULT '',
    db_port          INTEGER,
    db_name          TEXT NOT NULL DEFAULT '',
    db_username      TEXT NOT NULL DEFAULT '',
    db_password_ref  TEXT NOT NULL DEFAULT '',
    db_password_mask TEXT NOT NULL DEFAULT '',
    is_default       INTEGER NOT NULL DEFAULT 0,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    is_deleted       INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_test_env_name
    ON biz_test_env(name) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_biz_test_env_default
    ON biz_test_env(is_default) WHERE is_deleted = 0;
