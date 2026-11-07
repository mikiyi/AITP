-- App 自动化框架的运行时依赖路径
-- iOS / Android 用 Appium（appium 命令行 + Appium-Python-Client），
-- 鸿蒙 HOS 用 Hypium（hypium 包 + DevEco 自带的 hdc 命令行）。
-- 两项都留空表示按 PATH 查找，装在别处时在「设置 → 自动化管理设置」填绝对路径。
INSERT OR IGNORE INTO sys_config
    (config_key, config_value, value_type, scope, description, is_system, created_at, updated_at)
VALUES
    ('automation.appium.command', '', 'string', 'global',
     'Appium 命令行工具绝对路径，例如 C:\tools\appium\appium.cmd；留空按 PATH 查找', 1,
     datetime('now'), datetime('now')),
    ('automation.hdc.command', '', 'string', 'global',
     '鸿蒙 hdc 命令行工具绝对路径，例如 D:\devtools\hdc.exe；留空按 PATH 查找', 1,
     datetime('now'), datetime('now'));
