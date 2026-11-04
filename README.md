# AITestPilot (AITP)

个人 AI 测试工作台 —— **单用户、本地优先、离线可用**的质量管控工具。

把 AI 实验室、测试用例、缺陷、日志分析、SQL 分析、回归、报告、知识库、工作台配置收进同一条闭环：
**需求 → 测试点 → 用例 → 执行 → 缺陷 → 回归 → 报告 → 知识沉淀**。

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

- Python 3.11+
- Node.js 20+（仅首次构建前端需要）

---

## 功能地图

前端 21 条路由 / 21 个页面组件，后端 12 个蓝图统一挂在 `/api/v1` 下。

### 工作台首页

- 概览指标：项目数、用例数、缺陷数、待审知识数
- 快捷入口：12 个 AI 能力直达
- 近期报告与缺陷动态

### AI 实验室

12 项能力（前 10 项为实验室主能力，后 2 项为辅助能力）：

| 能力 | 标识 | 说明 |
|---|---|---|
| 需求评审 | `requirement_review` | 评审需求描述的完整性、可测性与歧义 |
| 测试点脑图 | `testpoint_generation` | 从需求拆出测试点并输出脑图大纲 |
| 用例生成 | `case_generation` | 生成可执行的测试用例 |
| XMind 转用例 | `xmind_to_case` | 把 xmind 大纲转成测试用例 |
| Bug 分析 | `bug_analysis` | 从缺陷描述定位根因与修复方向 |
| 日志排查 | `log_analysis` | 从日志片段定位异常链路 |
| SQL 分析 | `sql_analysis` | 校验并优化 SQL |
| 回归清单 | `regression_generation` | 基于改动范围产出回归范围 |
| 报告生成 | `report_generation` | 产出测试报告草稿 |
| 进度看板 | `project_progress_board` | 汇总项目进度与风险 |
| 知识问答 | `knowledge_qa` | 基于正式知识库回答并给引用 |
| 提示词优化 | `prompt_optimize` | 把粗略描述改写成结构化提示词 |

- **智能路由**：输入一句话，自动匹配最合适的能力。
- **会话记录**：每次运行留痕，可回看输入、输出、模型、用量与降级情况。
- **流式输出**：SSE 增量返回，首选模型失败时先弹降级提示再切模型。

### 测试资产

| 模块 | 能力 |
|---|---|
| 项目 | 项目增删改查、当前项目切换 |
| 需求 | 需求维护、关联模块、批量删除、CSV/JSON 导出 |
| 测试用例 | 用例 CRUD、状态流转、xlsx 导入导出、批量执行与批量删除、CSV/JSON 导出 |
| 缺陷 | 缺陷 CRUD、状态机流转、关联用例、日志附件、缺陷分析、批量删除、CSV/JSON 导出 |
| 知识库 | 待审池 → 正式库、版本、审核、引用保护、全文检索、批量通过驳回、CSV/JSON 导出 |
| 测试报告 | 报告生成/采纳、ECharts 图表、md/html/pdf/print 导出 |

> 列表页的筛选与分页会写进地址栏，链接可直接分享，刷新或粘贴链接都能还原当前视图；
> 导出走的是列表当前筛选条件，不是全量导出。

### 四级模块树

`项目 → 模块 → 子模块 → 功能`，模块树支持增删改、换父节点，并可为每个功能节点单独绑定 AI 能力
（启用开关 + 专属提示词 code 覆盖全局提示词）。

### 工作台配置

| 页面 | 内容 |
|---|---|
| AI 配置 | 供应商档案 CRUD、API Key 入密钥库、优先级排序、**真实自检**、**模型列表**、生效状态与来源层 |
| 知识库设置 | 存放目录、文件模式（mirror/db_only）、启动重建索引、**目录迁移**、**重建索引** |
| 模块树管理 | 四级结构维护与能力绑定 |
| 系统配置 | 全量配置项读写 |
| 数据备份 | 一键备份、下载、删除、恢复（恢复前自动生成一份安全备份，恢复后就地生效无需重启） |
| 审计日志 | 关键写操作留痕，可按资源追溯 |

> 页面头部常驻 **AI 状态条**：显示当前生效的 provider / model 与来源层（工作台配置 / 环境变量 / 内置默认），
> 鼠标悬停可看到降级顺序与数据外发边界。

---

## AI 接入方式

三层优先级：**工作台配置（数据库） > 环境变量 > 内置默认**。

1. **环境变量**（开箱即用，参考 [.env.example](.env.example)）
   - 通用：`AITP_AI_PROVIDER` / `AITP_AI_BASE_URL` / `AITP_AI_API_KEY` / `AITP_AI_MODEL`
   - 分厂商：`OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` / `OLLAMA_HOST` …
2. **工作台配置**：AI 配置页新增供应商档案，设置优先级（数值越大越优先）。
3. **本地大模型**：Ollama（`ollama_native` 协议，模型列表走 `GET /api/tags`）。

### 优先级与自动降级

- 本地规则即时返回；AI 调用受模型延迟与超时约束，默认 **60 秒**（可在「设置 → 系统配置」改 `ai.timeout.default`）。
- 本地模型与云端模型**混排排序**，按优先级从高到低尝试。
- 首选模型不可用（连不上 / 超时 / 鉴权失败 / 模型不存在）时，自动降级到下一个候选，
  前端只弹 **3 秒** toast 提示，不阻塞主流程。
- 全部候选都不可用时降级到**本地规则**；本地规则也没有结果时给出可读提示，绝不静默失败。
- **自检是真实调用**：返回 ✓/✗、延迟、回复片段、token 用量与可操作的中文提示
  （例如「无法连接本地模型服务：请先启动 Ollama（ollama serve）」）。未保存的配置与临时 Key 也能直接自检。

---

## 数据与知识治理

- 正式知识条目以 **Markdown 落盘**（`knowledge/`），SQLite 只存索引与元数据；数据库是权威数据源，
  写入顺序为「DB 提交 → 写 .md → 回写校验和」。
- **待审池**：AI 产出先进待审池，人工审核通过才成为正式条目并计入数字资产。
- **版本**：编辑已发布条目产生新版本，旧版本仍可检索，直到新版本审核通过；支持版本对比。
- **引用保护**：被下游引用的条目禁止撤销与删除，接口返回引用清单。
- **撤销可恢复**：撤销后退出全文检索，但记录保留可追溯。
- **索引重建**：文件被外部改过或丢失时，以文件为准回填数据库并重建全文索引。

---

## 安全边界

- **仅本机**：默认绑定 `127.0.0.1`；改为其它地址需显式设置 `AITP_ALLOW_PUBLIC_BIND=1`。
- **无 CORS**：服务端不返回任何 `Access-Control-*` 头，阻断恶意网站跨域读取本地 API。
- **密钥**：API Key 存于操作系统密钥库（Windows DPAPI / macOS 钥匙串），数据库只存掩码，
  **任何接口都不回传明文**，临时 Key 只在内存中参与一次调用。
- **静态托管**：路径规范化并剥离前导 `../`，生产只服务 `dist/`，有 `index.html` 兜底，防目录穿越。
- **注入面**：全部数据库操作为参数化查询；表名、列名、排序字段一律来自代码内白名单。
- **AI 外发**：调用第三方模型时输入内容会离开本机（含手机号、身份证、内部 SQL、生产日志等敏感信息），
  请先脱敏或改用本地模型 / 自托管服务；页面对每次调用都会提示外发边界。
- **AI 行为安全**：证据不足时明确提示而非编造；不编造知识标题或缺陷编号；AI 不批准上线；
  P0/P1 与 Prompt 测试结果必须人工复核。

---

## 质量门禁

```bash
python -m mypy                          # 后端类型门禁（--strict，94 个源文件）
python backend/scripts/smoke_api.py     # API 冒烟（229 项）
python backend/scripts/check_security.py     # 密钥/注入/路径安全回归
python backend/scripts/check_types.py        # mypy 门禁封装
python backend/scripts/check_lab.py          # AI 实验室能力回归
python backend/scripts/check_prompts.py      # 提示词库校验
python backend/scripts/check_knowledge_governance.py  # 知识治理回归
python backend/scripts/check_db.py           # 迁移与数据层回归
python backend/scripts/check_fts.py          # 全文检索回归
python backend/scripts/verify_xlsx.py        # xlsx 导入导出回归
```

前端类型与构建：

```bash
cd frontend && npm run build   # vue-tsc --noEmit && vite build
```

---

## 目录结构

```
backend/
  aitp/
    api/          12 个蓝图：workbench/lab/ai/projects/requirements/modules
                  test-cases/bugs/knowledge/reports/settings/meta
    ai/           resolver（优先级解析）、client（降级）、adapters（openai_compat/ollama_native）
    common/       常量、校验、分页、密钥库、安全工具
    db/           连接与迁移
    repositories/ 参数化 SQL 仓储层
    services/     知识落盘、报告导出、xlsx、xmind、备份恢复、AI 自检
    prompts/      提示词模板库
    static/       前端构建产物（由 npm run build 生成）
  prompts/        提示词 YAML
  scripts/        门禁与初始化脚本
frontend/
  src/
    api/          统一请求层（仅 request.ts / stream.ts 允许出现 fetch）
    components/   通用组件
    composables/  useEchart 等
    views/        21 个页面组件
data/             运行时数据（SQLite、日志、密钥、导出文件）
data/backups/     数据库备份快照（与 SQLite 同级，恢复前会自动留一份安全备份）
knowledge/        知识库 Markdown 落盘目录
logs/deploy.log   部署日志
```

---

## API 约定

- 前缀 `/api/v1`，HTTP 恒为 200，业务结果在包封的 `code` 里（`200000` 为成功）。
- 包封：`{ code, message, data, traceId, timestamp }`。
- 分页：`{ list, pageNum, pageSize, total, pages }`，默认每页 20 条，最大 100 条。
- 关键写操作写审计（`sys_audit_log`），可在设置页按资源追溯。

| 错误码 | 含义 |
|---|---|
| 100100 | 参数不合法 |
| 100103 / 100104 | 接口不存在 / 方法不支持 |
| 110102 / 110103 / 110105 / 110108 / 110110 | AI 凭证缺失 / 鉴权失败 / 不可达 / 模型不存在 / 超时 |
| 120102 | 提示词变量缺失 |
| 140100 / 140102 / 140103 / 140104 / 140105 | 资源不存在 / 非法流转 / 唯一冲突 / 被引用 / 非法操作 |

---

## 初始化与演示数据

```bash
python backend/scripts/init_db.py        # 初始化数据库与迁移
python backend/scripts/seed_demo.py      # 写入演示项目、模块树、用例、缺陷、知识条目
```

`.env.example` 复制为 `.env` 后按需修改（`.env` 不会进版本库）。
