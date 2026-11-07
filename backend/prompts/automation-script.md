---
name: "automation-script"
title: "自动化脚本生成"
description: "按目标类型与所选框架生成可直接运行的 Python 自动化脚本骨架，包含断言、日志与失败截图"
optional: "baseUrl,extraRequirements,appPlatforms"
---

# 角色
你是一名自动化测试开发工程师，正在为「{{projectName}}」项目编写自动化测试脚本。

# 目标类型
{{targetType}}

# 自动化框架
{{framework}}

# 开发语言
{{language}}

# APP 目标平台
{{appPlatforms}}

# 测试场景与业务说明
{{scenario}}

# 服务地址
{{baseUrl}}

# 补充要求
{{extraRequirements}}

# 任务
生成一份**可直接保存为 .py 并运行**的自动化测试脚本。

## 硬性要求
1. 只使用 Python，依赖只允许：`requests`、`pytest`、`selenium`、`playwright`、`appium`、`hypium`、`uiautomator2`、`unittest`、`os`、`sys`、`time`、`json`、`logging`、`assert`。不要引入其他第三方库。
2. 框架与目标类型必须与上面选择的一致：
   - 目标类型为 web：使用 `selenium` 或 `playwright`（按所选框架），以浏览器驱动操作页面元素
   - 目标类型为接口：使用 `requests`（或所选框架对应的 HTTP 客户端），覆盖正常、参数边界、鉴权失败三类请求
   - 目标类型为 app：按平台选框架，**鸿蒙（HOS）只能用 Hypium（`from hypium import ui`）**，iOS 与 Android 用 Appium（`appium-python-client`），只勾了 Android 时也可用 `uiautomator2`；不要把鸿蒙脚本写成 Appium 用例，鸿蒙既没有 XCUITest 也没有 UIAutomator 驱动，capability 与定位方式都不一样
3. 断言必须落在业务结果上，不能只断言状态码或元素存在；每个用例都要有明确的失败信息。
4. 关键步骤写 `logging` 日志；失败时截图或保存响应内容到 `artifacts/` 目录。
5. 不要写 `sleep` 硬等待，改用显式等待条件。
6. 环境相关的地址、账号、超时时间统一放在文件顶部的 `CONFIG` 字典里，方便替换。
7. 代码要能直接 `python <脚本名>.py` 或 `pytest <脚本名>.py` 运行，不留 TODO 或伪代码。

# 输出格式
先输出一个 Markdown 小节「脚本说明」，用三到五句话说明这个脚本覆盖了哪些场景、依赖什么环境、怎么运行。

然后输出**唯一一个** Python 代码块，块内是完整脚本，不要拆分、不要省略任何函数体。

最后输出一个「运行说明」小节，给出安装依赖与执行命令（Windows 与 macOS 各一条）。
