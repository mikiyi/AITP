# AITP 工程约定（Agent 必读）

面向 AI 编码代理的项目护栏。人类可读的完整开发 / 门禁 / E2E 文档见 `README.md`
（「质量门禁」「端到端测试」两节）；本文件只保留代理必须遵守的硬约束，不重复细节。

## 强制规则：改完代码必须跑验证，结果写进回复

**每次修改任何代码（前端 / 后端 / 门禁）后，必须运行验证，并把真实通过/失败结果写在该轮回复里。**
不允许只说"已修复"而不附运行结果；不允许用"看起来对了"代替真实 PASS/FAIL。

最小验证集（按改动范围选择，但 E2E 冒烟必跑；在项目 venv 内执行）：

```bash
# 1) 类型（前后端 + e2e 用例）
cd frontend && npx vue-tsc --noEmit && npx tsc -p tsconfig.e2e.json --noEmit
cd .. && python -m mypy --config-file mypy.ini backend/aitp backend/scripts

# 2) 确定性门禁（不烧模型额度；check_ai_live 已被 runner 排除）
python backend/scripts/run_all_checks.py

# 3) 端到端（隔离工装，不碰正式服务与正式库）
cd frontend && npx playwright test
```

## 项目硬约束

- 分支：提交默认落 `feature`；`master`/`develop` 为保护分支。不主动 commit/push，除非明确要求。
- 保密：`data/`、`.env`、`backend/aitp/static`、模型 Key 不入 Gitee/GitHub。
- 隔离：验证一律用隔离端口与临时库，绝不触碰正式服务（默认 8760）与 `data/aitp.db`；
  不擅自重启/停止正在运行的正式服务。
- 成本：E2E 与门禁一律用本地 mock 模型档案，不烧云端额度。

## E2E 要点

位置 `frontend/e2e/`，配置 `frontend/playwright.config.ts`；一键 `cd frontend && npx playwright test`。
工装自动拉起/清理隔离环境，teardown 树杀进程并删临时目录。
每步断言业务结果，不只断言"没报错"；页面变化后重新定位元素，不复用旧 ref；
非快乐路径（取消/放弃保存、空数据、超大量、未登录/无权限、刷新/后退、表单边界）必覆盖。
遍历策略、端口、用例清单与已知限制见 `README.md`「端到端测试」。