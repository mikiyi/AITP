# AITP 工程约定（Agent 必读）

## 强制规则：改完代码必须跑测试，结果写进回复

**每次修改任何代码（前端 / 后端 / 门禁）后，必须运行下面的验证，并把真实通过/失败结果写在该轮回复里。**
不允许只说"已修复"而不附运行结果；不允许用"看起来对了"代替真实 PASS/FAIL。

最小验证集（按改动范围选择，但 E2E 冒烟必跑）：

```bash
# 1) 类型（前后端）
cd frontend && npx vue-tsc --noEmit
.venv/Scripts/python.exe -m mypy --config-file mypy.ini backend/aitp backend/scripts

# 2) 确定性门禁（不烧模型额度；check_ai_live 已被 runner 排除）
.venv/Scripts/python.exe backend/scripts/run_all_checks.py

# 3) 端到端（隔离工装，不碰正式 8760 / data/aitp.db）
cd frontend && npx playwright test
```

## E2E 运行方式

- 位置：`frontend/e2e/`，配置 `frontend/playwright.config.ts`。
- 一键：`cd frontend && npx playwright test`（串行 workers=1）。
- 工装自动拉起/清理：独立后端 **8762** + 临时库、mock 模型 **18790/18791**、vite **5199**。
  绝不触碰正式 8760 与 `data/aitp.db`；teardown 树杀进程并删临时目录。
- 首次需浏览器：`npx playwright install chromium`。
- 结果：`frontend/e2e/results.json`（JSON）+ 控制台 list；失败留 trace/截图。
- 遍历策略：BFS 全路由一次访问（无白屏/JS 异常/5xx）；DFS 核心页穷尽交互，**每页≤20 元素**防死循环。
- 非快乐路径必覆盖：返回/取消/放弃保存、空数据、超大量、未登录/无权限、刷新/后退、表单边界（空/超长/特殊字符）。
- 每步断言业务结果，不只断言"没报错"；页面变化后重新定位元素，不复用旧 ref。
- 用例文件：`01-bfs-smoke`(全路由)、`02-core-dfs`(核心业务+BUG-1/2)、`03-nonhappy`(非快乐路径)、`04-auth`(口令)、`05-prod-confirm`(BUG-3 生产守卫 API 级)。
- 已知限制：生产确认**勾选弹窗**的 UI 交互在 Playwright 自带浏览器下渲染不稳定，故 `05` 用 API 级守卫断言；弹窗 UI 已在有头浏览器人工验证。首启引导会在隔离库自动弹出，setup 已 `onboarding complete` 关闭遮罩。

## 其它约定

- 提交默认落 `feature` 分支；`master`/`develop` 为保护分支。不主动 commit/push，除非用户明确要求。
- `data/`、`.env`、`backend/aitp/static`、模型 Key 不入 Gitee/GitHub。
- 不擅自重启/停止用户正在用的正式 8760；验证用隔离端口。
- 真实模型额度：E2E 一律用本地 mock 档案，不烧云端额度。
