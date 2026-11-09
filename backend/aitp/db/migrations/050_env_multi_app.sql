-- =====================================================================
-- AITP 迁移 050：应用服务器支持多台
--   一套环境可能对应多台应用服务器（网关、多个节点、前后端分离部署）。
--   新增子表，一行一台；旧的 app_base_url / app_health_path / app_metrics_url
--   保留不删，作为「首台」的兼容镜像，旧代码与旧备份仍可读。
-- =====================================================================

CREATE TABLE IF NOT EXISTS biz_test_env_app_server (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    env_id      INTEGER NOT NULL REFERENCES biz_test_env(id),
    base_url    TEXT NOT NULL DEFAULT '',
    health_path TEXT NOT NULL DEFAULT '',
    metrics_url TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biz_test_env_app_server_env
    ON biz_test_env_app_server(env_id) WHERE is_deleted = 0;

-- 把现有单台应用服务器搬进子表；重复执行不会产生重复行
INSERT INTO biz_test_env_app_server
    (env_id, base_url, health_path, metrics_url, sort_order, created_at, updated_at, is_deleted)
SELECT
    e.id, e.app_base_url, e.app_health_path, e.app_metrics_url, 0,
    e.created_at, e.updated_at, 0
FROM biz_test_env e
WHERE e.is_deleted = 0
  AND e.app_base_url <> ''
  AND NOT EXISTS (
      SELECT 1 FROM biz_test_env_app_server s
      WHERE s.env_id = e.id AND s.is_deleted = 0
  );
