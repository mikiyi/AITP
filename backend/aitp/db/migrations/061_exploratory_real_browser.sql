-- =====================================================================
-- AITP 迁移 060：探索性测试 MCP 命令从 mock 桩切到真实浏览器服务
--   历史演示/门禁把 exploratory.web.settings.mcpCommand 配成了
--   mock_mcp_server.py（确定性桩，不拉浏览器），导致真实探索无法拉起浏览器。
--   这里把仍指向 mock 的配置替换为真实浏览器服务 browser_mcp_server.py；
--   用户自定义的其它命令不动。mock 仍保留供门禁/演示显式选用。
-- =====================================================================

UPDATE sys_config
SET config_value = REPLACE(config_value, 'mock_mcp_server.py', 'browser_mcp_server.py'),
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE config_key = 'exploratory.web.settings'
  AND config_value LIKE '%mock_mcp_server.py%';