# AITestPlatform (AITP)

[English](README.en.md) | 简体中文

个人版的AI 测试工作台 —— **单用户、本地优先、离线可用**、**提示词工程，大模型调用数据脱敏**的质量管控工具。

把 AI 实验室、测试用例、缺陷、日志分析、SQL 分析、回归、报告、知识库、任务清单、工作台配置利用AI的LLM辅助测试工作，收进同一条闭环：
**需求 → 测试点 → 用例 → 执行 → 缺陷 → 回归 → 报告 → 知识沉淀**，中间随时把待办推进度挂在清单里。

- 数据全部保存在本机 SQLite（`data/aitp.db`），默认仅本机可访问，不对局域网/公网暴露。
- AI 能力直接读取本地环境变量中的 AI 配置信息获取，同时支持工作台内配置与本地大模型（Ollama）。
- 没有云端模型、模型不可用或断网时，自动降级到本地规则，不阻塞主流程。

---

## 快速开始

**Windows**

```bat
start.bat
```

**macOS / Linux**

```bash
./start.sh
```

首次运行会创建 `.venv`、安装依赖、构建前端，随后在 <http://127.0.0.1:8760> 启动。

停止服务：

```bat
stop.bat          :: Windows
```

```bash
./stop.sh         # macOS / Linux
```

### 启动脚本参数

| 参数 | 说明 |
|---|---|
| `--build` | 强制重新构建前端 |
| `--reinstall` | 重建虚拟环境并重装依赖 |
| `--no-browser` | 启动时不自动打开浏览器 |

脚本会先检查端口：若 AITP 已在运行则直接打开页面，不会重复启动；若端口被其它程序占用则明确报错退出。

### 环境要求

- Python 3.11+（3.11 / 3.12 为全门禁验证版本；3.13 可用，numpy 会自动换到 2.x）
- SQLite 3.34+ 且带 FTS5 trigram（启动时自检；系统自带 Python 过旧会直接提示换解释器）
- Node.js 20+（仅首次构建前端需要）

---

## 功能地图

前端 45 条路由 / 46 个页面组件，后端 33 个蓝图 / 310 个接口统一挂在 `/api/v1` 下。

### 首页

- 概览指标：项目数、用例数、缺陷数、待审知识数
- 快捷入口：常用入口 Top 10，按真实点击次数排序（含 AI 能力与模块树、资产、系统设置入口），没点过的按原顺序补位
- 近期报告与缺陷动态
- 卡片可定制：见 **设置 → 首页卡片定制**（`/settings/home-cards`）

### 外观与导航

- **10 套主题**：白、黑、蓝、红、粉、橙、科技、海洋、植物、工业。右上调色盘一次点选即全站生效。
- **导航分组**：侧栏按用途分成 AI 实验室 / 测试预案 / 测试资产 / **质量分析** / 自动化 / 知识库 / 设置 七个组。
- **筛选即查询**：列表页筛选项一改就自动出结果，不再需要点「查询」；筛选与分页仍写进地址栏，链接可分享。

### 任务清单

页面：**任务清单**（`/todos`）

- 顶部速览：未完成 / 今天到期 / 逾期 / 已完成 / 完成率
- 左侧视图：全部任务、今天到期、本周到期、已逾期、置顶、已完成
- 中间清单：快速录入、筛选、批量操作与导出
- 右侧详情：状态、优先级、到期日、分类、标签、所属项目、进度与说明，可挂子任务
- 子任务完成度自动回推父任务

### AI 实验室

19 项能力，基于知识库的问答走「知识库 → 问答助手」的对话面板：

| 能力 | 标识 |
|---|---|
| 需求确认 | `requirement_confirm` |
| 测试点脑图 | `testpoint_generation` |
| 用例生成 | `case_generation` |
| XMind 转用例 | `xmind_to_case` |
| 缺陷分析 | `bug_analysis` |
| 日志排查 | `log_analysis` |
| SQL 分析 | `sql_analysis` |
| 回归清单 | `regression_generation` |
| 报告生成 | `report_generation` |
| 进度看板 | `project_progress_board` |
| 接口测试设计 | `api_test_design` |
| 测试计划 | `test_plan` |
| 用例评审 | `case_review` |
| 缺陷归类 | `bug_classification` |
| 风险评估 | `risk_assessment` |
| 自动化脚本生成 | `automation_script` |
| 性能脚本生成 | `performance_script` |
| 测试数据生成 | `test_data_generation` |
| 提示词优化 | `prompt_optimize` |

- **能力卡片由提示词库驱动**
- **智能路由**：输入一句话，自动匹配最合适的能力
- **执行前预检**：跑之前先体检，列出缺项、完整度、主/辅助任务与执行顺序
- **会话记录**：每次运行留痕，可回看输入、输出、模型、用量与降级情况
- **流式输出**：SSE 增量返回，首选模型失败时先弹降级提示再切模型

### 测试资产

| 模块 | 能力 |
|---|---|
| 项目 | 项目增删改查、当前项目切换、状态管理 |
| 需求 | 需求维护、关联模块、批量删除、导出 |
| 测试用例 | 用例 CRUD、状态流转、xlsx 导入导出、批量执行 |
| 缺陷 | 缺陷 CRUD、状态机流转、关联用例、日志附件 |
| 知识库 | 待审池 → 正式库、版本、审核、引用保护、全文检索 |
| 测试报告 | 报告生成/采纳、ECharts 图表、指标趋势、导出 |
| 回归清单 | 清单 CRUD、AI 生成回归条目、明细逐条回写执行结果 |

### 工作台配置

| 页面 | 内容 |
|---|---|
| AI 配置 | 供应商档案 CRUD、API Key 入密钥库、优先级排序、真实自检、模型列表 |
| 知识库设置 | 存放目录、文件模式、启动重建索引 |
| 模块树管理 | 三级结构维护与能力绑定 |
| 系统配置 | 全量配置项读写 |
| 测试环境配置 | 多套命名环境、口令入系统密钥库、可达性探测 |
| 数据备份 | 库快照备份、整包备份、恢复 |
| CI 与通知 | CI 令牌、Webhook 渠道、事件订阅、测试发送 |
| 首页卡片定制 | 首页非固定卡片的显隐与排序 |

---

## AI 接入方式

三层优先级：**工作台配置（数据库） > 环境变量 > 内置默认**

1. **环境变量**（开箱即用）
   - 通用：`AITP_AI_PROVIDER` / `AITP_AI_BASE_URL` / `AITP_AI_API_KEY` / `AITP_AI_MODEL`
   - 分厂商：`OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` / `OLLAMA_HOST` …
2. **工作台配置**：AI 配置页新增供应商档案，设置优先级
3. **本地大模型**：Ollama（`ollama_native` 协议）

### 优先级与自动降级

- 本地规则即时返回；AI 调用受模型延迟与超时约束，默认 **60 秒**
- 本地模型与云端模型**混排排序**，按优先级从高到低尝试
- 首选模型不可用时，自动降级到下一个候选，前端只弹 **3 秒** toast 提示
- 全部候选都不可用时降级到**本地规则**
- **自检是真实调用**：返回 ✓/✗、延迟、回复片段、token 用量

---

## 数据与知识治理

- 正式知识条目以 **Markdown 落盘**（`knowledge/`），SQLite 只存索引与元数据
- **待审池**：AI 产出先进待审池，人工审核通过才成为正式条目
- **问答助手三种模式**：
  - `llm`：只用模型自身知识作答（外发）
  - `kb`：纯本地检索并直接摘出原文（不外发）
  - `hybrid`（默认）：先检索知识库，把命中正文带 `[K1]…` 标记喂给模型整合（外发）

---

## 安全边界

- **仅本机**：默认绑定 `127.0.0.1`
- **同源防护**：写接口必须来自本机页面
- **本地访问口令（可选）**：在「设置 → 访问口令」启用，12 小时过期
- **密钥存储**：API Key 存于操作系统密钥库（Windows DPAPI / macOS 钥匙串 / Linux Fernet）
- **AI 外发脱敏**：云端调用默认自动替换手机号、身份证、邮箱、银行卡号、API Key 等
- **AI 行为安全**：证据不足时明确提示而非编造；不编造知识标题或缺陷编号；AI 不批准上线；P0/P1 与 Prompt 测试结果必须人工复核

---

## 质量门禁

```bash
python -m mypy                          # 后端类型门禁
python backend/scripts/smoke_api.py     # API 冒烟
python backend/scripts/check_security.py     # 密钥/注入/路径安全回归
python backend/scripts/check_db.py           # 迁移与数据层回归
python backend/scripts/check_fts.py          # 全文检索回归
cd frontend && npm run build   # vue-tsc --noEmit && vite build
```

---

## 目录结构

```
backend/
  aitp/
    api/          33 个蓝图 / 310 个接口
    ai/           resolver、client、masking、adapters
    common/       常量、校验、分页、密钥库、安全工具
    db/           连接与迁移
    repositories/ 参数化 SQL 仓储层
    services/     知识落盘、报告导出、备份恢复、AI 自检等
    prompts/      提示词模板库
    static/       前端构建产物
  prompts/        提示词 YAML
  scripts/        门禁与初始化脚本
frontend/
  src/
    api/          统一请求层
    components/   通用组件
    views/        46 个页面组件
data/             运行时数据（SQLite、日志、密钥）
knowledge/        知识库 Markdown 落盘目录
```

---

## 初始化与演示数据

```bash
python backend/scripts/init_db.py        # 初始化数据库与迁移
python backend/scripts/seed_demo.py      # 写入演示数据到 data/demo.db
```

`.env.example` 复制为 `.env` 后按需修改（`.env` 不会进版本库）。

---

## 许可证

MIT License