# AITestPlatform (AITP)

[English](README.en.md) | 简体中文

个人版的AI 测试工作台 —— **单用户、本地优先、离线可用**、**提示词工程，大模型调用数据脱敏**的质量管控工具。

当前版本 **v2.7.0**：与后端 `aitp.__version__`、`db/migrator.py::APP_VERSION`、`GET /api/v1/health` 的
`appVersion` 以及迁移记录的 `app_version` 同源，一处升级四处一致。

把 AI 实验室、测试用例、缺陷、日志分析、SQL 分析、回归、报告、知识库、任务清单、工作台配置利用AI的LLM辅助测试工作，收进同一条闭环：
**需求 → 测试点 → 用例 → 执行 → 缺陷 → 回归 → 报告 → 知识沉淀**，中间随时把待办推进度挂在清单里。

这条闭环转起来之后，资产会自己「滚雪球」——你用得越多，AI 越懂你的项目：

**AI 产出 → 入库成为资产 → 执行留证据 → 沉淀为知识 → 反哺下一次生成 → …**

刚上手时，AI 对你的项目一无所知，生成的用例难免泛泛；用一段时间之后就不一样了：

1. **AI 产出的用例、脚本**，一键入库成为你的资产，和手工建的放在一起管理；
2. **入库的资产被执行**——接口与自动化真跑、功能用例人工录入结果——哪些通过、哪些失败都留了底；
3. **踩过的坑、验证过的结论**，「加入知识库」审核转正，沉淀为项目知识；
4. **下一次生成时**，与输入相关的用例、缺陷与知识被自动装进提示词——AI 生成时已经知道功能点在哪、历史出过什么缺陷、踩过什么坑。

库越用越准，产出越来越贴项目实际——这就是「资产复利」。

- 数据全部保存在本机 SQLite（`data/aitp.db`），默认仅本机可访问，不对局域网/公网暴露。
- AI 能力直接读取本地环境变量中的 AI 配置信息获取，同时支持工作台内配置与本地大模型（Ollama）。
- 没有云端模型、模型不可用或断网时，自动降级到本地规则，不阻塞主流程。

## Eval 子包预览（v0.1，2026-11）——「也能测 AI 软件」

AITP 过去解决的是「用 AI 测软件」。这一版补上另一半：**测 AI 软件**。

- **数据模型**：迁移 `089_eval_core.sql` 十张表——评测集 / 用例 / 配置 / 判定器 / 跑批 / 逐条结果 / 指标 / 校准 / 门禁 / 门禁结论
- **纯领域层**：`backend/aitp/eval/`（指标、断言、门禁求值，零 IO、可单测）
  - 指标带 **95% Wilson 区间**；`min` 类门禁卡**下界**、`max` 类卡**上界**（对 max 用下界等于放宽）
  - 风险加权：S0/S1/S2 = 50/10/1，一条「把不能买说成能买」≈ 50 条格式错误
  - 样本分级：n<30 只出逐条证据，n<200 只出 warn，n≥200 才判 pass/fail
- **跑批**：`eval_service.execute_replay()` 回放评测（输出由调用方提供：真实调用、线上留痕、人工构造都行），落逐条结果 + 指标 + 门禁结论 + Markdown 报告
- **接口**：`/api/v1/evals` 12 条路由（评测集/用例/跑批/执行/门禁/报告）
- **门禁与演示**：`check_eval_metrics.py`（6 项断言全过）、`eval_demo.py`（12 条金融题，good → pass / bad → fail）
- **对拍工具**：`tools/eval_toolkit/` 用 Promptfoo 跑同一批用例，规则断言 **24/24 一致**；Ragas 最小 demo 演示 faithfulness 归因

```bash
python backend/scripts/check_eval_metrics.py   # 指标与判定链门禁
python backend/scripts/eval_demo.py            # 端到端演示（示例数据，非真实模型成绩）
python tools/eval_toolkit/build_config.py      # 生成 Promptfoo 配置
```

**数据边界**：引擎开源、评测集私有。仓库内只有公开规则构造的演示用例（T+1 交收、印花税、适当性等），不含任何真实业务样本与客户数据。

- **口径核对**：迁移 86 份（至 089）、`/api/v1` 路由 524 条 / 蓝图 49 个、确定性门禁 84 项（83 个 `check_*.py` + `smoke_api.py`）

设计说明见 [EVAL.md](EVAL.md)。

## v2.7.0 更新（执行路径收口 + 平台化外联）

- **App 场景引擎**：场景自动化补齐最后一条执行路径，App 场景页签原生可用；步骤支持启动 / 停止 / 点选 / 输入 / 滑动 / 返回 / Home / 等待 / 文本断言 / 截图十类动作，复用 adb 驱动与统一证据口径，文本断言基于 `uiautomator dump` 真读界面，截图落 `data/app-shots/`；adb 命令、设备序列号、命令超时可在场景页配置并「测试连接」列出在线设备
- **通知模板（渠道配置化）**：通知渠道新增类型与模板——Webhook / 钉钉 / 企微 / 飞书 / 邮箱共用 `{{eventLabel}}`、`{{title}}`、`{{projectName}}`、`{{occurredAt}}` 等变量，支持标题 + 正文模板、变量一键插入与「渲染预览」；邮件走 SMTP，密码存本机密钥库，接口只回 `hasPassword`，测试发送与真实告警走同一条渲染链路
- **自动化失败 → 缺陷半自动流转**：新增 `automation.auto_bug_mode` 三档——关闭 / 待审（默认）/ 自动，在「性能测试管理设置」用单选控件切换（保存时同步旧布尔键，后端只接受三档枚举、其余值入口拒绝报错）；待审档把场景与自动化的失败先写成「缺陷待审」，缺陷列表顶部集中展示来源与实际结果，人工「确认转缺陷」才落正式缺陷并关联用例，「忽略」即终态
- **AITP MCP 服务端（只读优先）**：`POST /api/v1/mcp` 提供 JSON-RPC 2.0 的 `initialize` / `tools/list` / `tools/call` / `ping`；只读工具覆盖项目、用例、需求、轮次、缺陷与质量洞察，写工具 `bugs.draft.create` 仅在显式开启写权限时可用且只写待审草稿；默认关闭、仅回环访问、支持 Bearer 令牌与调用留痕；令牌明文只在生成/轮换时展示一次，历史令牌可停用/启用/删除并记录最后使用与客户端
- **跨轮次失败聚类**：最近 N 轮失败按「标题 + 实际结果」归一化签名聚类，给出出现次数、涉及用例、来源轮次、首末出现时间与逐轮趋势，把重复翻车与偶发失败分开看
- **用例依赖与变更影响面**：可维护用例间依赖（同项目、禁自依赖、禁成环）；选定变更用例与需求后，按「直接变更 + 需求关联 + 反向依赖 BFS」算出受影响用例并附最近失败率，排本轮必测范围有据可依
- **质量工程**：迁移 087（通知模板字段、缺陷待审、用例依赖、MCP 调用日志与默认配置）与 088（MCP 令牌哈希入库、轮换与启停记录）、`check_v27.py` 门禁与端到端 spec 21；接口增至 511 个 / 48 蓝图，版本升到 v2.7.0

## v2.6.0 更新（执行可信度 + 资产效率）

- **失败归因**：接口执行失败自动分为「疑似缺陷 / 断言错误 / 环境问题 / 数据问题」，规则可解释并随证据落库；环境与数据类失败会在修复建议入口直接拦截，提示先修环境，避免把基础设施问题写成断言修改
- **flaky 检测与稳定性重试**：失败可按 0–5 次重试，重试后通过或最近 5 次结果翻转标记「不稳定」；报告与发布结论把 flaky 与真失败分列，减少环境抖动造成的误判
- **用例局部 Diff 修订**：选中一批用例后输入一条指令（补异常场景 / 改预期 / 调优先级 / 删重复），AI 产出逐条 patch，采纳才写回并留审计；无模型时明确提示，不阻塞手工维护
- **发布就绪结论**：发布准入页给出「可发布 / 可发但有风险 / 不可发布」三档结论，聚合 P0 失败、未闭环缺陷、覆盖缺口、flaky 与环境类失败，并给出可执行建议
- **执行前置自检**：批量执行前检查用例范围、环境地址、登录态、接口 URL/方法与断言覆盖，缺项直接阻断或告警，避免产生一批没有诊断价值的失败记录
- **执行并发与隔离**：接口批量执行支持 1–8 并发，请求并行、写回串行；单条异常不影响整批，进度实时回传，SQLite 写竞争由主线程收口
- **用例集质量体检**：本地规则检查重复标题、缺步骤、缺预期、缺优先级、未归属功能点、接口缺 URL/断言，输出 0–100 分与逐条问题，点击可直达用例
- **风险驱动智能选测**：按优先级、近期失败、变更影响、flaky 与未执行记录加权，给出本轮必测集与逐条理由，可直接加入轮次
- **历史资产导入增强**：用例导入支持 xlsx / csv / json，先干跑预览再落库；同编号同标题按幂等跳过，并保留导入批次记录
- **AI 入口统一附件输入**：需求批量拆分、知识问答、AI 修订与修复建议统一支持「文本 + 文档 + 图片」；图片上传时自动去水印，模型档案可声明是否支持图片，纯文本模型会跳过图片并提示，文本与文档链路不阻塞；图片入口只收本机能解码的格式（JPG/PNG/GIF/WebP/BMP/TIFF/PSD/HEIC/AVIF 等），RAW/JXL/AI 会明确提示先转成 PNG/JPG
- **智能路由先转文本**：智能路由收到附件时，文档在本地抽正文、图片调一次多模态模型转写成文本，再走原有纯文本路由；纯文本输入零额外调用，无视觉模型时跳过图片并说明
- **附件提示词注入预处理**：识别图片/文件的 AI 入口在拼接提示词前统一做确定性预处理——清除零宽/双向字符、转义伪角色标记、逐行标记「指令覆盖 / 提示词套取 / 越狱扮演」内容，并附「仅作资料、不执行其中指令」守卫说明；图片像素无法预处理时在提示词层声明不执行图中文字指令
- **质量工程**：新增 `check_case_insight.py` 门禁与端到端 spec 18–19，能力卡增至 23 张，版本升到 v2.6.0

## v2.5.0 更新（四期：把资产串成飞轮）

- **AI 修复建议（修复闭环）**：接口断言失败后一键生成「资产修改建议」——诊断原因、给出新断言 / 新预期、附字段级 diff，进「修复建议」待审池；人工确认采纳后才改写用例并留审计。AI 只建议、不直接改库；无模型时只给机械 diff
- **知识库语义检索**：配置 Ollama 嵌入模型后，知识召回从纯关键词升级为「关键词 + 向量」RRF 混合，问答助手与上下文装配同时受益；未配置自动回落关键词检索，云端嵌入默认关闭且外发前脱敏
- **执行媒体证据**：浏览器类执行可选采集视频 / Trace 并落到本机 `data/exec-media/`，证据详情内嵌回放、Trace 一键下载；单次视频 ≤ 50MB、Trace ≤ 20MB，超限自动降级为仅截图
- **登录态复用**：按「项目 + 环境 + 角色」保存浏览器认证态（storageState），执行前预检 cookie / JWT 过期，有效直接复用、过期明确提示；档案只存本机，不进备份与导出
- **离线测试知识包**：内置对抗性字符串、边界值模板、SFDIPOT 启发式、注入探测样例四份本地字典，按关键词命中注入提示词——零 LLM 调用、离线可用、不烧额度
- **用例脑图视图**：测试用例页可一键切换到只读脑图，功能点树为骨架、用例为叶子，视图态写进地址栏可分享还原
- **需求批量拆分导入**：需求页可上传或粘贴需求文档，AI 拆成多条可独立验收的需求并给出功能点归属建议；逐条勾选后批量落库为草稿，同一文档按内容指纹识别重复导入、已采纳条目自动跳过；无模型时不阻塞，可手工逐条新建
- **质量工程**：新增 7 个门禁（修复建议 / 向量检索 / 执行媒体 / 登录态 / 知识包 / 脑图 / 需求导入）与 5 条端到端用例（13–17），版本升到 v2.5.0

## v2.4.5 更新

- **缺陷编号全局唯一**：手工建单 `BUG-001`、AI 处理产生 `AI_BUG-001`、自动化脚本/场景/矩阵产生 `Auto_BUG-001`，三类各自自增；缺陷号全局唯一，软删后永不复用（截图文件名不再被覆盖）；老库跨项目重号在启动迁移时自动顺延到最大号之后
- **缺陷截图**：缺陷详情支持上传、预览、下载、删除截图；文件落在 `data/bug-screenshots/<缺陷编号>/`，命名 `pic_<缺陷编号>_001.png` 按序递增；单张 ≤ 10MB、每个缺陷 ≤ 30 张，只收 PNG/JPEG/GIF/WebP/BMP 位图
- **备份开关**：「设置 → 数据备份与还原 → 备份内容」新增「备份包含缺陷截图」，默认关闭；打开后整包备份、项目资产包、留存归档都带上截图，关闭时三处都不包含（截图仍留本机目录，不参与留存清理）
- **AI 供应商接入方式**：内置厂商扩容，选厂商后可切「按量 API / 订阅套餐（Agent / Coding / Token Plan）」，自动带出接口地址、协议与 Key 格式提示；新增 Anthropic Messages 协议适配，套餐档案按 token 额度扣减、不计金额
- **AI 设置界面**：页面拆为「LLM设置 / 外发脱敏」两个 Tab，脱敏开关独立成页；档案弹窗去掉临时 Key 与计费模式单选（由接入方式自动派生），「启用本地 Key」前移到优先级前，「测试大模型配置是否连通」居中放在底栏
- **质量工程**：新增 `check_bug_screenshots.py` 门禁与端到端 spec 12（编号前缀、全局唯一、不复用、截图命名/上限/删除、三处开关）

## v2.4.4 更新

- **AI 用例产出导出**：「用例编号」列对未入库用例填「草稿-001」草稿序号；采纳入库或导入时由系统分配正式编号，草稿号不会落库
- **自动化脚本**：新建脚本自动归属当前项目并带默认框架与起步正文；目录统计按当前项目收敛；定时调度目标从场景/脚本下拉选择；执行与批量执行防重复触发；编辑支持变更说明
- **场景与数据库脚本表单**内置变量用法提示（含示例）；复制场景同样计入脚本引用库；调度「下次运行」显示本地时间
- **质量工程**：e2e 用例纳入类型检查（`tsconfig.e2e.json`）；新增代码噪声门禁；全站死代码与前端重复代码清理

## 运行演示

下面两个演示均跑在**本地 Ollama mock 模型**上（`docs/screenshots/` 下是真实运行截图，非设计稿）：

**AI 能力卡片 · 用例生成** —— 选能力卡 → 填需求原文 → 本地模型产出结构化用例（含正向/异常/边界/接口多类），可导出 Excel/Word 或一键采纳为用例：

![AI 能力卡片用例生成演示](https://gitee.com/mikiyi/AITP/raw/master/docs/screenshots/demo-ai-capability.gif)

**知识库问答助手** —— 手工录入知识条目进待审池 → 提问 → 助手先检索知识库、再由模型整合，回答带 `[K1]` 等引用来源标注：

![知识库问答助手演示](https://gitee.com/mikiyi/AITP/raw/master/docs/screenshots/demo-knowledge-assistant.gif)

> 演示中默认档案为本地 mock 模型，不消耗任何云端额度；换成云端 Key 后同一套链路即可调用真实大模型。

**首页工作台** —— 数据概览、知识库问答入口、卡片自定义：

![首页工作台](https://gitee.com/mikiyi/AITP/raw/master/docs/screenshots/01-dashboard.png)

**测试用例** —— 按功能点树浏览、筛选、导入导出、覆盖矩阵：

![测试用例列表](https://gitee.com/mikiyi/AITP/raw/master/docs/screenshots/02-cases.png)

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

前端 59 条路由 / 64 个页面组件，后端 48 个蓝图 / 511 个接口统一挂在 `/api/v1` 下。

### 首启引导与示例项目

- 空数据库首次启动，首页自动弹出四步引导：配置 AI 模型 → 载入示例项目 → 跑一次 AI 生成 → 看报告与追溯；可跳过，完成或跳过后不再自动弹出，存量库升级默认不打扰。
- 「载入示例项目」以追加方式注入 `DEMO-EC` 示例（需求 / 用例 / 缺陷 / 知识 / 报告 + 可执行接口断言脚本 `AUTO-API-001`），只重建示例自身、不动既有数据，可重复点击重建；项目列表空态也能一键载入。
- 帮助页（`/help`）右上角「重新打开首启引导」可随时重看；状态接口 `GET/POST /api/v1/onboarding`、注入接口 `POST /api/v1/onboarding/demo`。

### 首页

- 概览指标：项目数、用例数、缺陷数、待审知识数
- 快捷入口：常用入口 Top 10，按真实点击次数排序（含 AI 能力与模块树、资产、系统设置入口），没点过的按原顺序补位
- 近期报告与缺陷动态
- 卡片可定制：见 **设置 → 首页卡片定制**（`/settings/home-cards`）

### 外观与导航

- **10 套主题**：白、黑、蓝、红、粉、橙、科技、海洋、植物、工业。右上调色盘一次点选即全站生效。
- **导航分组**：侧栏按用途分成 10 组——AI 实验室 / 任务中心 / 测试预案 / 测试资产 / 质量结论 / 自动化脚本 / 执行与探索 / 知识库 / 数据治理 / 设置，另有首页与帮助文档两个直达项；进入某页时所在分组自动展开，手动展开/收起的组与侧栏折叠态都记在本机。
- **筛选即查询**：列表页筛选项一改就自动出结果，不再需要点「查询」；筛选与分页仍写进地址栏，链接可分享。

### 任务清单

页面：**任务清单**（`/todos`）

- 顶部速览：未完成 / 今天到期 / 逾期 / 已完成 / 完成率
- 左侧视图：全部任务、今天到期、本周到期、已逾期、置顶、已完成
- 中间清单：快速录入、筛选、批量操作与导出
- 右侧详情：状态、优先级、到期日、分类、标签、所属项目、进度与说明，可挂子任务
- 子任务完成度自动回推父任务

### AI 实验室

23 项能力，基于知识库的问答走「知识库 → 问答助手」的对话面板：

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
| Web 探索性测试 | `exploratory_web` |
| 提示词优化 | `prompt_optimize` |
| 修复建议 | `fix_suggestion` |
| 需求批量拆分 | `requirement_import` |
| 用例局部修订 | `case_patch` |

- **测试数据脱敏样本（GAP-39）**：测试数据生成能力新增「脱敏样本」模式，CSV / JSON / XLSX 样本在本机解析，自动识别手机号、邮箱、身份证、卡号、口令等敏感列，按「掩码 / 哈希 / 随机 / 枚举 / 占位」规则脱敏；库内只存列结构与脱敏副本，原始样本不落盘，方案可保存复用，样本按保留期自动清理

- **产出一键入库**：测试数据生成（SQL）的造数语句可一键「添加为数据库脚本」，自动化脚本生成按产出代码类型一键「添加为 web/app/接口自动化脚本」，入库自动带 `AI_` 前缀编号并进对应菜单，角标实时更新；用例类产出导出 Excel 时未入库用例填「草稿-001」草稿序号，采纳入库或导入时由系统分配正式编号（草稿号不会落库）

- **能力卡片由提示词库驱动**
- **智能路由**：输入一句话，自动匹配最合适的能力
- **执行前预检**：跑之前先体检，列出缺项、完整度、主/辅助任务与执行顺序
- **上下文装配**：按项目与功能点召回模块、既有用例、缺陷与知识注入提示词，档位「精简 / 标准 / 完整」，默认标准档（GAP-27）
- **输出结构校验**：产出按 JSON Schema 校验，字段缺失或类型错位当场拦截，并给出可重试的中文说明（GAP-28）
- **生成去重**：与库内已有用例逐条比对，判定模式「标记疑似重复 / 按基线增量补充」可配，疑似项默认不勾选（GAP-29）
- **成本核算**：按云端供应商档案单价折算每次调用的 token 与金额，日/月预算与单次 token 上限只做软提示、不拦截（GAP-30）；本地（自托管）模型不产生云服务费，不参与计费
- **关闭思考模式**：档案级开关，给 qwen3 / DeepSeek-R1 这类推理模型跳过思考阶段直出答案（本地 qwen3:8b 站内问答实测 57 秒 → 13 秒）；下发字段随协议自动选择（Ollama 原生 `think=false`，OpenAI 兼容 `reasoning_effort=none` + `chat_template_kwargs.enable_thinking=false`）
- **会话记录**：每次运行留痕，可回看输入、输出、模型、用量与降级情况
- **流式输出**：发起后立即返回任务号，前端按 `framesUrl` 增量轮询取帧（长连接不再占用同步 worker），首选模型失败时先弹降级提示再切模型

### 测试资产

| 模块 | 能力 |
|---|---|
| 项目 | 项目增删改查、当前项目切换、状态管理 |
| 需求 | 需求维护、直挂功能点（模块/子模块/功能点三级）、批量删除、导出 |
| 测试用例 | 用例 CRUD、状态流转、xlsx 导入导出、批量执行 |
| 缺陷 | 缺陷 CRUD、状态机流转、关联用例、日志附件、原始执行证据下钻；编号全局唯一并带来源前缀（手工 `BUG-` / AI `AI_BUG-` / 自动化 `Auto_BUG-`，软删不复用）；详情页可上传/预览/下载/删除截图（`data/bug-screenshots/<缺陷编号>/`，`pic_<编号>_001` 递增，单张 10MB、单号 30 张）；自动化/场景失败按「关闭 / 待审（默认）/ 自动」三档流转，待审档先入列表顶部「缺陷待审」区，人工确认才落正式缺陷并关联用例，「忽略」即终态 |
| 断言库 | 可复用断言规则按项目/功能点共享，接口用例以 `lib:编号` 引用，支持 JSON Schema 断言与失败差异对比 |
| 数据库查询检查点 | 接口断言新增 `db:` 目标（`db:` 比首行首列 / `db:rows:` 比行数 / `db:json:` 比结果 JSON），场景新增「数据库」步骤；连接取自执行环境（口令走 OS 密钥库），只读强制（拒绝写操作/多语句/INTO）、超时与 200 行上限，`{{var}}` 参数化；适配 MySQL/PostgreSQL 协议族国产库（OceanBase/TiDB/GBase/金仓/瀚高/openGauss/GaussDB/神通）、达梦、ClickHouse、SQLite、MongoDB、Redis，驱动懒加载缺装给提示；查询语句与结果进统一证据 |
| 知识库 | 待审池 → 正式库、版本、审核、引用保护、全文检索 |
| 测试报告 | 报告生成/采纳、ECharts 图表、指标趋势、导出 |
| 执行与证据 | 接口 / 自动化 / 手工 / 回归 / **探索性**五条执行路径统一留一份证据快照，轮次、缺陷、报告三处可下钻到原始请求响应与日志 |
| 回归清单 | 清单 CRUD、AI 生成回归条目、自动条目由服务端真跑并留证据、手工条目回写强制附证据 |
| 执行矩阵（GAP-43） | 一批用例 × 多环境 × 多端（应用服务器）一次调度逐格真跑，逐用例跨环境对比归因（普遍通过 / 普遍失败 / 环境特有 / 端特有 / 混合），普遍失败一键转缺陷并挂证据；生产环境调度需显式确认 |
| 质量洞察 | 跨轮次失败聚类：最近 N 轮失败按「标题 + 实际结果」归一化签名聚类，给出出现次数、涉及用例、来源轮次、首末出现时间与逐轮趋势；变更影响面：维护用例依赖（同项目、禁自依赖、禁成环），按「直接变更 + 需求关联 + 反向依赖 BFS」圈出受影响用例并附最近 20 次失败率 |
| 场景自动化 | 无代码编排的多步骤串联场景，页内分 **Web 场景 / App 场景 / 接口场景** 三个页签（App 场景原生步骤已接入：启动 / 停止 / 点选 / 输入 / 滑动 / 返回 / Home / 等待 / 文本断言 / 截图十类动作，adb 驱动，文本断言基于 uiautomator dump 真读界面，截图落 `data/app-shots/`；adb 命令、设备序列号、命令超时在页内配置并可「测试连接」列出在线设备）；步骤可选接口 / 浏览器 / 数据库 / **自动化脚本** 四类（自动化脚本步骤按脚本编号引 Web/App/接口自动化脚本并随场景同步执行、执行记录进统一证据，复用自动化引擎不另造执行轮子），每步复用接口断言引擎（不另造 HTTP 与断言轮子）：JSONPath 提取变量后用 `{{var}}` 传给后续步骤的 URL / 头 / 参数 / 体（数据库步骤的查询结果同样可提取），失败可「即停」或「继续」，支持数据池逐条驱动整个场景，每次执行落历史。场景带 SCN 编号与启用/停用状态，列表支持名称/编号关键词与状态筛选，状态可在新建/编辑表单落库。命名口径：脚本 = 写 Python 代码（Web / App / 接口自动化脚本，编号前缀 AUTO-W/AUTO-A/AUTO-I，性能脚本 PERF，数据库脚本 SQL；AI 一键入库的数据库脚本走独立序列 **AI_SQL-xxx**，自动化脚本走 **AI_AUTO-W/AI_AUTO-A/AI_AUTO-I/AI_PERF-xxx**，一眼区分来源），场景 = 无代码编排；定时调度带 SCH 编号。脚本「已被引用」口径=被场景自动化的 automation 步骤引用（浮显引用方场景的 SCN 编号，定时调度与历史执行记录都不算引用），悬停按钮浮显引用方编号，顿号分隔最多 10 个，超出走「查看更多」弹框列全部引用；复制出来的场景同样计入引用库。数据库脚本与场景步骤表单内置变量用法提示（含 {{var}} 示例），新建自动化脚本默认带可用框架与起步正文，脚本目录统计按当前项目收敛，定时调度目标从场景/脚本下拉选择 |
| AI 能力一键入库 | AI 实验室产出可直接转为资产：测试数据生成（SQL）的造数语句一键「添加为数据库脚本」，自动化脚本生成按产出代码类型一键「添加为 web/app/接口自动化脚本」，入库自动带 `AI_` 前缀编号并进对应菜单，角标实时更新 |
| 独立数据池 | 跨用例与场景共享一份测试数据，项目内名称唯一、支持解析预览；用例扩展字段与场景数据源统一按 `pool:<名称>` 引用，解析复用用例测试数据口径，不另造取数逻辑 |
| Web 探索性测试（GAP-45） | MCP 驱动真实浏览器（`backend/scripts/browser_mcp_server.py`，由「探索性测试设置」里的 `mcpCommand` 一条完整启动命令拉起）按「读快照 → 决策 → 动作 → 观测」循环探索，无脚本发现「用例追不上功能」的漏网问题；正常步按「单步截图数」留证，异常步另强制 3 张截图 + 双锚点 ±N 秒日志切片 + 网络快照 + 控制台错误 + traceId 组成证据包；口令/令牌输入按 PII 规则脱敏；产物先进待审池人工审核，通过才转缺陷 |
| AITP MCP 服务端 | `POST /api/v1/mcp`（JSON-RPC 2.0：initialize / tools/list / tools/call / ping），默认关闭、仅回环、可选 Bearer 令牌；只读工具覆盖项目 / 用例 / 需求 / 轮次 / 缺陷 / 质量洞察，写工具 `bugs.draft.create` 需显式开启写权限且只写待审草稿；令牌明文只在生成/轮换时展示一次，历史记录保留掩码、状态与最后使用/客户端，可停用/启用/删除；每次调用留痕 `sys_mcp_call_log` |
| 知识 / 报告片段跨项目复用（GAP-42） | 片段在来源项目维护一份，目标报告登记引用关系，渲染 / 导出实时读取正文，避免多份副本漂移；被引用的片段禁止删除 |
| 数据留存与归档（GAP-44） | 按数据类型（轮次 / 自动化执行 / 证据 / 日志 / 待办 / 缺陷截图）配置保留天数与到期动作（归档 / 软删 / 仅留摘要）；引用保护（缺陷引用的证据、矩阵引用的记录到期不清）；到期数据压缩归档到 `data/archive/` 并可检索回看原文；支持干跑预览与启动自动清理 |

### 工作台配置

| 页面 | 内容 |
|---|---|
| AI 配置 | 供应商档案 CRUD、API Key 入密钥库、优先级排序、真实自检、模型列表、档案单价与调用成本、上下文装配档位与去重模式、预算软阈值 |
| 知识库设置 | 存放目录、文件模式、启动重建索引 |
| 模块树管理 | 三级结构维护（子节点层级角色按父节点递进：模块→子模块→功能）、能力绑定与专属提示词 |
| 系统配置 | 全量配置项读写 |
| 测试环境配置 | 多套命名环境；每套可挂多台应用服务器并逐台探测、逐台画资源曲线；数据库类型覆盖 MySQL / PostgreSQL / SQL Server / Oracle / SQLite、国产库（达梦、人大金仓、瀚高、openGauss、GaussDB、OceanBase、TiDB、GBase、神通 OSCAR、ClickHouse）与非关系库（MongoDB、Redis、Elasticsearch、Cassandra、InfluxDB）；口令入系统密钥库 |
| 数据备份 | 库快照备份、整包备份、恢复；「备份包含缺陷截图」开关（默认关闭）控制整包 / 项目资产包 / 留存归档是否带截图 |
| 数据留存与归档 | 各数据类型保留天数与到期动作、到期预览、干跑统计、立即清理、归档区检索与原文回看（GAP-44） |
| 探索性测试设置 | 最大步数 / 时长 / 单步超时 / 单步截图数（异常步固定另补 3 张）、记录选择器、输入脱敏开关、日志切片窗口与来源、MCP 服务命令与钉住工具清单（GAP-45） |
| 项目资产包 | 结构包导出 / 预览 / 改名导入：模块、版本、需求、用例、缺陷、知识、脚本、断言规则；id 全部重分配、外键重映射，不含密钥与执行历史 |
| 外部平台导入 | 禅道 / Jira / TestLink 只读单向导入：登录换 token 后只发 GET，绝不回写；禅道与 Jira 导需求 / 用例 / 缺陷，TestLink 导需求 / 用例；凭据入系统密钥库，支持「平台账号 + 服务器级 HTTP Basic」两层认证；映射模板可自定义，按 项目 + 平台 + 外部 id 去重，批次留痕 |
| CI 与通知 | CI 令牌、通知渠道（Webhook / 钉钉 / 企微 / 飞书 / 邮箱）、渠道类型 + 标题/正文模板 + 变量插入 + 渲染预览、邮件 SMTP（密码入系统密钥库，接口只回 `hasPassword`）、订阅事件、测试发送与发送记录 |
| MCP 服务 | `POST /api/v1/mcp` 开关、允许写入开关、端点与令牌状态（生成/轮换后明文只展示一次）、令牌记录（停用/启用/删除、创建与最后使用时间、最后调用客户端）、工具清单、最近调用留痕 |
| 首页卡片定制 | 首页非固定卡片的显隐与排序 |

---

## AI 接入方式

三层优先级：**工作台配置（数据库） > 环境变量 > 内置默认**

1. **环境变量**（开箱即用）
   - 通用：`AITP_AI_PROVIDER` / `AITP_AI_BASE_URL` / `AITP_AI_API_KEY` / `AITP_AI_MODEL`
   - 分厂商：`OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` / `SENSENOVA_API_KEY` / `MINIMAX_API_KEY` / `BIGMODEL_API_KEY` / `XIAOMI_API_KEY` / `MOONSHOT_API_KEY` / `ARK_API_KEY` / `HUNYUAN_API_KEY` / `SILICONFLOW_API_KEY` / `OPENROUTER_API_KEY` / `GITEE_AI_API_KEY` / `OLLAMA_HOST` …
2. **工作台配置**：AI 配置页新增供应商档案，设置优先级
3. **本地大模型**：Ollama（`ollama_native` 协议）

内置供应商（填 Key 即用）：OpenAI、通义千问、DeepSeek 深度求索、商汤日日新、**MiniMax（国内 / 国际）**、**智谱 BigModel**、**小米 MiMo**、**Kimi 月之暗面（国内 / 国际）**、**火山引擎方舟（豆包）**、**腾讯云混元**、**硅基流动 SiliconFlow**、**OpenRouter**、**模力方舟（Gitee AI）**、自定义兼容接口；本地为 Ollama。

- **接入方式（按厂商自动出现）**：同一供应商可在「按量 API / 订阅套餐（Agent / Coding / Token Plan）」间切换，切套餐会自动带出对应接口地址、协议与 Key 格式提示（如小米 `token-plan-cn.*` + `tp-/ttp-`、MiniMax / Kimi 的 Coding 套餐走 Anthropic 兼容端点）。
- **协议**：OpenAI 兼容（`openai_compat`）、Anthropic Messages 兼容（`anthropic`，覆盖官方与各家 Coding 套餐）、Ollama 原生（`ollama_native`）。

### 优先级与自动降级

- 本地规则即时返回；AI 调用受模型延迟与超时约束，默认 **60 秒**
- 本地模型与云端模型**混排排序**，按优先级从高到低尝试
- 首选模型不可用时，自动降级到下一个候选，前端只弹 **3 秒** toast 提示
- 全部候选都不可用时降级到**本地规则**
- **自检是真实调用**：返回 ✓/✗、延迟、回复片段、token 用量

### Token Plan 套餐模式

AI 档案除 **API 按量**外支持 **Token 套餐**（`billing_mode=token_plan`，迁移 060）：

- 预购 token 额度（`token_quota`），用套餐凭证调用同一 OpenAI 兼容接口，接口与鉴权方式不变；
- 每次调用按 `totalTokens` 扣减额度（`token_used`），界面显示剩余 = 额度 − 已用；
- 套餐模式**不按量计金额**（`cost_micros` 落 NULL），与「未配价只报 token」口径一致；
- `token_quota=0` = 未设额度，只统计不扣减；额度扣减用 SQL 自增，并发不丢计数；
- **本地（自托管）档案不参与计费**：既不配单价也不选套餐，创建/更新时计费字段一律归零，
  成本接口把它标成 `isLocal=true` + 不计费，给它的单价写接口直接拒绝。

---

## 数据与知识治理

- 正式知识条目以 **Markdown 落盘**（`knowledge/`），SQLite 只存索引与元数据
- **待审池**：AI 产出先进待审池，人工审核通过才成为正式条目
- **问答助手三种模式**：
  - `llm`：只用模型自身知识作答（外发）
  - `kb`：纯本地检索并直接摘出原文（不外发）
  - `hybrid`（默认）：先检索知识库，把命中正文带 `[K1]…` 标记喂给模型整合（外发）
- **问答历史按会话留痕**：每次一问一答都写进会话（能力标识「知识库问答」），换个菜单再回来、刷新、重开浏览器都能回放；「清空对话」= 另起一条会话，问过的内容仍在库里，去「AI 记录 → 会话记录」按能力筛选可查；回放会带回答模式、引用来源与留档时间，因此重点「加入知识库」会被判定为重复而不会二次入池；问答会话不写 AI 执行记录，不进成本与预算口径

---

## 性能与可靠性

- **列表不整表吐数**：列表接口走服务端分页（`pageNum` / `pageSize`，单页上限 100），用例树按 `afterId` 游标逐批续取；前端表格在行数超过阈值时启用虚拟窗口，只渲染视口内的行
- **长任务出请求线程**：批量验证 run-all、大文件导出、AI 流式执行都改为后台任务，接口只回任务号，进度与产物按任务查
- **写库加固**：进程内写串行化 + `SQLITE_BUSY` 有界重试，自拟编号的取号与插入在同一事务内完成，避免并发创建撞唯一键
- **可观测性**：每个请求带 `traceId`，贯穿响应头与结构化日志；日志只记异常与慢请求；`/api/v1/health` 返回数据库延迟、写队列与 AI 配置的真实状态
- **异常不静默**：捕获到的异常要么记一行带 `traceId` 的日志继续，要么转成包封错误码，不留裸 `pass`；前端接口失败有可见提示与重试入口，不白屏

---

## 安全边界

- **仅本机**：默认绑定 `127.0.0.1`
- **同源防护**：写接口必须来自本机页面
- **本地访问口令（可选）**：在「设置 → 访问口令」启用，12 小时过期
- **密钥存储**：API Key 存于操作系统密钥库（Windows DPAPI / macOS 钥匙串 / Linux Fernet）
- **AI 外发脱敏**：云端调用默认自动替换手机号、身份证、邮箱、银行卡号、API Key 等
- **AI 行为安全**：证据不足时明确提示而非编造；不编造知识标题或缺陷编号；AI 不批准上线；P0/P1 与 Prompt 测试结果必须人工复核
- **生产环境执行统一确认**：轮次 / 接口批量 / 自动化 / 执行矩阵凡涉及生产环境，统一弹「已知晓风险」勾选确认（默认不勾选、不勾不能执行），后端同样强制 `confirmProd`，UI 与 API 双重守卫
- **终态锁定防绕过**：编排发布后锁定编辑/删除；报告发布/归档为终态、归档不可退回草稿；轮次已完成结果冻结——定稿资产不会被悄悄改回

---

## 质量门禁

一条命令跑全部确定性门禁（runner 自动发现 `backend/scripts/check_*.py` 加 `smoke_api.py`，
按字典序逐个执行，末尾输出汇总表，任何失败退出码非 0）：

```bash
python backend/scripts/run_all_checks.py                                  # 全量，单脚本默认超时 300s
python backend/scripts/run_all_checks.py --list                           # 只列会跑哪些脚本
python backend/scripts/run_all_checks.py --only check_db,check_fts        # 子串匹配只跑部分
python backend/scripts/run_all_checks.py --skip-name check_walkthrough --timeout 600
python backend/scripts/run_all_checks.py --json logs/gates.json           # 写 JSON 报告，供 CI 归档
cd frontend && npm run build                                              # 前端类型 + 构建（vue-tsc --noEmit && vite build）
```

- runner 先探测 `http://127.0.0.1:8760/api/v1/health`（可用 `--base-url` 或 `AITP_BASE_URL` 覆盖）；
  探测不到时明确警告，并把需要服务的脚本（`check_isolation_matrix`）记为 FAIL 且提示
  「需先启动本地服务」（`python backend/run.py`）。runner 不会自动启动服务，也不会假装通过。
- `--isolated`：不必先手工启动服务——runner 用 SQLite 备份 API 把正式库取成一份一致性快照，
  在空闲端口起一台一次性临时服务（数据库与日志都写在临时目录，正式库只在取快照时以只读方式访问），
  把 `AITP_BASE_URL` 与库/日志路径注入每个子进程；runner 退出时按进程树结束服务并删除临时目录。
  密钥与提示词仍指向正式路径，否则快照里的加密列解不开；与 `--base-url` 互斥，CI 不需要它（干净 runner 上没有正式库可快照）。
  带它的理由是实测出来的：不加时 `check_isolation_matrix` 这类打 HTTP 的门禁会在正式库留下软删行（完整套件
  BEFORE/AFTER 实测 +2/+7/+4/+6 行，全部 `is_deleted=1`），加上同一只门禁 `PASS=191 FAIL=0` 而正式库 delta 为 0。
- `check_ai_live` 被 runner 永久排除：它打真实商汤模型接口、烧额度且非确定性，需要时单独手工跑。
- 新增门禁脚本只要放进 `backend/scripts` 且命名 `check_*.py`，runner 自动纳入，无需改 runner。

门禁清单（83 个脚本 = 82 个 `check_*.py` + `smoke_api.py`，与 `backend/scripts` 下真实脚本一一对应；其中 `check_ai_live` 不进 runner，实际执行 82 项）：

| 脚本 | 含义 | 失败怎么查 |
| --- | --- | --- |
| `check_ai_live.py` | 真实大模型全路径测试（**不进 runner**，打商汤接口烧额度、非确定性） | 需要时手工单跑，见脚本头部环境变量说明 |
| `check_ai_masking.py` | AI 外发脱敏：手机号/身份证/邮箱/银行卡/API Key 自动替换 | 看 `backend/aitp/ai/masking` 规则与档案配置 |
| `check_ai_thinking.py` | 「关闭思考模式」全链路：`extra_json.thinking=off` → resolver 解析 → 两个适配器的请求体字段，未置位时不下发（默认语义不变） | 看 `ai/resolver.py::_disable_thinking` 与 `ai/adapters/*` 的 `_body` |
| `check_ai_cost.py` | AI 成本核算：档案单价与微元折算、日/月预算与单次 token 软阈值、超预算只提示不拦截（GAP-30） | 看 `backend/aitp/ai/cost.py` 与 `sys_ai_profile` 的单价字段 |
| `check_ai_local_billing.py` | 本地（自托管）档案不参与计费：写侧归零、单价接口拒绝、成本表与币种不被残留值带偏、实跑成本落 NULL | 看 `cost.is_local_profile` 与 `api/settings.py`、`api/lab.py` 的计费字段处理 |
| `check_api_scenario.py` | 多接口串联场景：顺序执行 + JSONPath 变量提取 + `{{var}}` 传到后续步骤、每步断言复用接口断言口径、失败即停、数据驱动逐条跑整个场景、执行历史落库、场景 automation 步骤引用自动化脚本并同步执行（自带 `/login` `/order` 桩服务） | 看 `services/api_scenario_service.py` 与 `api/api_scenarios.py` |
| `check_egress_guard.py` | 数据外发确认只看实际会用的首选模型：本地首选不打扰，云端首选才弹，候选链照旧完整返回 | 看 `knowledge_assistant_service.egress_plan` 与前端 `stores/aiGuard.ts` |
| `check_assistant_history.py` | 问答历史按会话落库：一问一答进 `biz_ai_session/biz_ai_message`（能力 `knowledge-assistant`）、回放带模式与引用、留档时间一致所以「加入知识库」不会重复入池、非法/别的模块会话不被放行、超上限只回放最近这些且不落 `biz_ai_record` | 看 `knowledge_assistant_service.history/record_qa` 与 `api/knowledge.py::_persist_qa` |
| `check_assistant_thinking.py` | 问答助手思考过程：`reasoningTokens` 进计费口径、ask-stream 走 SSE 且无可用 AI 配置时以 error 事件收尾、思考默认不落库、opt-in 才写 reasoning 且回放带 thinkingText | 看 `knowledge_assistant_service.record_qa/history` 与前端「仅当次会话可见」提示 |
| `check_async_races.py` | 异步调度竞态：矩阵并发双跑只允许一个成功、任务取消不再假成功（queued→running 竞态后按真实状态处理） | 看矩阵调度互斥与 `services/job_queue.py` 的取消分支 |
| `check_attachments.py` | 附件能力：文档导入 + 图片去水印 + AI 入口统一输入（文档拼提示词 / 图片多模态 / 视觉能力降级）+ 需求拆分、知识问答、AI 修订带附件 + 智能路由先转文本 + 提示词注入预处理（不可见字符 / 伪角色标记 / 指令覆盖标记） | 看附件接口、Pillow 依赖与临时 mock 服务日志 |
| `check_automation.py` | 自动化脚本资产与执行引擎：四类脚本 CRUD / 子进程执行留痕 / 结果回写；`from-output` 转脚本资产；AI 一键入库走独立编号（`AI_AUTO-W/AI_AUTO-A/AI_AUTO-I/AI_PERF` 与 `AI_SQL` 前缀），手工编号不受影响 | 看自动化执行记录与引擎日志（`data/aitp.log`） |
| `check_automation_writeback.py` | 自动化结果回写用例/轮次（GAP-07） | 核对回写服务与轮次状态机 |
| `check_backup_schedule.py` | 定时备份与保留策略（GAP-13） | 看备份服务与 `data/` 目录保留逻辑 |
| `check_bug_screenshots.py` | 缺陷编号与截图（迁移 074）：来源列与三类前缀（BUG / AI_BUG / Auto_BUG）、缺陷号全局唯一、软删不复用、取号进写事务、截图命名 `pic_<编号>_001` 与单张 10MB / 单号 30 张上限、删除后序号不回退、截图默认不进整包 / 资产包 / 留存 | 看 `db/migrations/074_bug_numbering_screenshots.sql` 与 `repositories/bug_repo.py::next_bug_no` |
| `check_case_test_data.py` | 用例多行步骤/预期按行配对 + 测试数据多格式解析（内联文本/JSON、txt/csv/xlsx/json/sql 文件、URL 与未知格式拒绝）、自动化执行消费 `AITP_TEST_DATA`、JMeter 指向解析后的 `testdata.csv`（BUG-44/45） | 看测试数据解析服务与用例步骤/数据落库 |
| `check_ci_boundary.py` | CI Token 访问边界：路由白名单、项目范围、有效期、吊销、审计区分（GAP-24） | 看 CI 令牌鉴权中间件与审计来源标记 |
| `check_ci_notifications.py` | CI 触发与 Webhook 通知（GAP-10），自带本地 HTTP 接收器 | 看令牌哈希、通知渠道生效范围与发送记录 |
| `check_context_assembler.py` | 上下文装配：三档限量、按项目/功能点召回模块/用例/缺陷/知识、预览与实装同口径（GAP-27） | 看 `backend/aitp/services/context_assembler.py` 的档位与召回 |
| `check_db.py` | 数据层：PRAGMA、外键约束、种子量、软删、并发写 | 看 `backend/aitp/db` 迁移与连接配置 |
| `check_db_checkpoint.py` | 数据库查询检查点（方案 A）：只读闸门（写操作/多语句/INTO 拒绝）、Redis 只读白名单、Mongo 查询解析、SQLite 真执行 `db:`/`db:rows:`/`db:json:` 断言与 `{{var}}` 参数化、行数上限、场景 database 步骤断言/变量提取、证据留查询语句、国产库缺驱动给安装提示 | 看 `services/db_checkpoint_service.py` 与 `interface_runner._eval_db_rule` |
| `check_db_concurrency.py` | 并发写与取号竞态：16 并发创建不撞唯一键、报成功数等于落库数、写锁统计（GAP-26） | 看 `backend/aitp/db/write_guard.py` 与各仓储取号 |
| `check_dedup.py` | 生成去重判定口径：归一化、阈值分档、mark/increment 行为、payload 下标对齐（GAP-29） | 看 `backend/aitp/services/dedup_service.py` |
| `check_env_config.py` | 测试环境配置：CRUD、多台应用服务器、国产/非关系库类型与默认端口、口令加密掩码、健康探测、默认环境唯一 | 看环境配置接口与密钥库落盘 |
| `check_error_boundary.py` | 异常治理与前端兜底：不吞异常、错误带 traceId、长执行出请求线程、空态与重试（GAP-26） | 看 `except Exception` 处置与 `validation_jobs` |
| `check_execution_evidence.py` | 统一执行证据协议：五条执行路径的必填字段矩阵、快照与缺失降级（GAP-32） | 看 `execution_protocol.py` 与 `biz_execution_evidence` |
| `check_execution_matrix.py` | 执行矩阵：多环境 × 多端逐格真跑、跨格对比归因、普遍失败转缺陷、生产确认（GAP-43） | 看 `execution_matrix_service.py` 与 `biz_execution_matrix*` |
| `check_exploratory_web.py` | Web 探索性测试：MCP 桩驱动、证据包字段、双锚点 ±5s 日志切片、产物进待审池非缺陷库、无模型降级、输入脱敏（GAP-45） | 看 `exploratory_service.py` 与 `biz_exploration_*` |
| `check_export_caps.py` | 导出能力宣称与实现一致：Excel/Word 白名单同源、不支持时给中文降级（GAP-37） | 看 `capability_codes` 白名单与帮助页口径 |
| `check_fts.py` | FTS5 混合检索：中文短词兜底、注入防护、软删过滤、索引同步 | 看检索服务与索引重建逻辑 |
| `check_functional_matrix.py` | 功能矩阵：多项目 × 边界值 × 全路径（临时库自跑） | 按失败项名定位对应模块接口 |
| `check_global_search.py` | 全局搜索能力边界 | 看全局搜索与各模块索引 |
| `check_home_cards.py` | 首页卡片定制：默认布局、脏配置清洗、显隐排序闭环 | 看首页配置读写与清洗逻辑 |
| `check_interface_runner.py` | 接口用例执行引擎：请求组装、断言、超时、同源限制、批量队列 | 看执行引擎与轮次回写 |
| `check_isolation_matrix.py` | 多项目 × 多版本数据隔离审计（HTTP 版，可长跑） | 需先启动本地服务；带 `AITP_ACCESS_TOKEN` 重跑单脚本 |
| `check_kb_ask.py` | 知识库问答检索兜底：自然语言问句能召回正式条目 | 看知识库检索与落盘目录 `knowledge/` |
| `check_knowledge_governance.py` | P8 知识治理：待审转正、版本化、引用保护、重建索引 | 看治理服务与 Markdown 落盘 |
| `check_knowledge_recall.py` | 知识语义召回评估（D1 条件项）：混合检索 vs 纯向量/关键词的召回命中对比 | 看召回评估脚本与 `docs/AITP-三期-D1-召回评估.md` |
| `check_knowledge_reuse.py` | 知识 / 报告片段跨项目复用：片段 CRUD、引用关系、渲染实时读正文、被引用禁删（GAP-42） | 看 `report_fragment_repo.py` 与 `biz_report_fragment*` |
| `check_lab.py` | P6 AI 实验室 | 看实验室能力清单与临时 mock 服务输出 |
| `check_numbering_uniqueness.py` | 编号唯一性兜底：需求 / 场景 / 场景执行主编号的唯一索引与列组合、同号拦截与跨项目放行、老库覆盖安装消歧（保留 id 最小一条、其余顺延到同组最大号之后、不误伤无重复行）、软删可复用、全部主编号列与取号调用点登记不漏项、需求外部导入幂等键与用例 / 缺陷同口径、矩阵取号持写锁 | 看 `db/migrations/075_no_unique_indexes.sql`、`repositories/numbering.py` 与 `services/execution_matrix_service.py` |
| `check_observability.py` | 可观测性：traceId 贯穿日志与响应头、只记异常/慢请求（GAP-23） | 看 `backend/aitp/observability` 与日志格式 |
| `check_onboarding.py` | 首启引导状态（GAP-18） | 看迁移 045 与 onboarding API |
| `check_output_schema.py` | 输出结构校验：四个能力的 JSON Schema、字段缺失与类型错位的拦截、重试口径（GAP-28） | 看 `backend/aitp/ai/output_schema.py` 与能力映射 |
| `check_p2.py` | P2 收口：定时调度 CRUD / 立即触发 / 到点 `_tick` 派发、执行指标聚合、JMeter 负载模型下发 `-Jthreads`、场景的浏览器步骤（mock MCP）；调度带 SCH 编号、脚本仅在被场景 automation 步骤引用时判「已被引用」且 refs 回带场景 SCN 编号（只有调度引用或执行记录都不算）、场景停用状态可落库而非法状态被拒 | 看 `api/schedules.py`、`services/test_schedule_service.py::_tick` 与 `services/automation_service.py::_jmeter_command` |
| `check_pagination.py` | 列表分页与游标续取：接口不再整表吐数、`total` 与列表条数一致、用例树按 `afterId` 翻页（GAP-25） | 看 `common/pagination.py` 与各列表接口 |
| `check_portability.py` | 跨平台交付：启动自检、PDF 中文字体、密钥库回落、脚本可移植性 | 按打印的平台相关检查项定位（不写正式库） |
| `check_prompt_regression.py` | 提示词效果回归集：离线 mock 跑批 + 六指标基线（通过率/保留率/完整度/重复率/问题数/提示词体积），改动可对比（GAP-31） | 看 `backend/prompts/regression/` 样例与基线文件 |
| `check_prompts.py` | 提示词库：模板可解析、变量齐全、渲染无残留、能力对齐 | 看 `backend/prompts` YAML 与能力映射表 |
| `check_report_evidence.py` | 报告失败样本与证据下钻：样本条数与真实失败一致、三种导出都带上所选样本、无快照给中文降级、证据只在用例级（手工执行）时报告/下钻/缺陷三处口径一致（GAP-35） | 看 `api/reports.py` 的 failure-samples 与 `execution_protocol.latest_evidence_for_run_case` |
| `check_report_sources.py` | 报告按轮次 / 执行矩阵 / 场景批次三种来源取数：来源解析、章节指标、统计接口、失败样本隔离、非法来源拒绝 | 看 `services/report_source_service.py` 与报告统计接口 |
| `check_report_trends.py` | 报告趋势与版本对比（GAP-12） | 看报告导出与趋势聚合 |
| `check_report_xss.py` | 报告 HTML / 打印导出的 Markdown 净化：`attr_list`、`javascript:` 链接与原始 HTML 不进导出文件，常规表格 / 代码块 / 安全链接照旧保留（只跑渲染层，不建库不调模型） | 看 `services/report_exporter.py` 的净化分支 |
| `check_residual.py` | 独立数据池 CRUD / 同名拦截 / 解析预览且 `pool:<名称>` 被场景数据驱动消费；轮次环境差异归因（环境敏感 / 一致失败 / 无基线 / 未绑定环境）；`automation.auto_bug` 默认关闭、开启后失败自动转缺陷且同一次执行只建一条 | 看 `repositories/data_pool_repo.py`、`services/auto_bug_service.py` 与轮次归因逻辑 |
| `check_retention.py` | 数据留存与归档：分类型保留期与到期动作、引用保护、软删 + zip 归档、原文回看、启动自动清理（GAP-44） | 看 `retention_service.py` 与 `data/archive/` |
| `check_routing.py` | 智能路由回归：23 张能力卡片各至少一条代表性输入必须路由正确 | 看路由规则与能力卡片配置 |
| `check_scope_enforcement.py` | 项目范围硬约束：报告片段、报告导出、场景运行三处跨项目不串数据（补充门禁） | 看各接口的 `projectId` 归属校验与片段引用解析 |
| `check_script_asset.py` | 脚本版本快照 / 差异 / 评审 + UI 元素库（Page Object，脚本按名称取选择器）+ 探索性测试产物转回放脚本 | 看 `repositories/script_asset_repo.py` 与 `api/automation.py` 的版本、元素库路由 |
| `check_secret_residue.py` | GAP-22 密钥入库收口：引用式存储、残留检测、迁移/回滚与双读 | 看密钥引用表与残留扫描逻辑（全在临时库，不碰正式库） |
| `check_stream_polling.py` | 流式短轮询契约两端对齐：发起只给 taskId + 取帧路由、帧序与游标、缓冲过期回落，以及前端确实按 `mode:"poll"` 去取帧、发起时带上当前项目（GAP-26 / GAP-29） | 看 `api/lab.py` 的 stream/frames 与 `frontend/src/api/stream.ts` |
| `check_security.py` | 安全与合规：密钥、注入、路径安全、同源防护 | 看 `backend/aitp/common` 安全工具与中间件 |
| `check_task_labels.py` | 任务类型标签：已注册 kind 与中文标签表双向相等、筛选下拉同源（GAP-37） | 看 `services/job_queue.py` 的 `KIND_LABELS` |
| `check_test_data.py` | 测试数据生成（文件分支） | 看生成服务与导出文件 |
| `check_test_data_samples.py` | 测试数据脱敏样本（GAP-39）：敏感列漏配拒绝、脱敏副本不含原始 PII、方案复用与过期清理 | 看样本池服务与脱敏引擎 |
| `check_project_package.py` | 项目资产包（GAP-41）：结构包计数、无密钥、同编码拒绝、改名导入与外键重映射 | 看资产包服务与 manifest |
| `check_external_import.py` | 外部平台只读导入（GAP-40）：桩禅道验证登录换 token、只发 GET 不回写、映射落库、外部 id 去重、两层认证、映射模板与批次隔离、密钥不落库 | 看 `services/external_import*` 与 `api/external_imports.py` |
| `check_test_execution.py` | 执行闭环：计划 → 轮次 → 批量结果 → 失败转缺陷 → 追溯矩阵 → 发布准入 | 按失败环节名定位对应蓝图 |
| `check_test_round_redesign.py` | 测试计划/轮次多轮改造：轮次类型、计划时间窗、范围预设、4/3 轮模板生成（迁移 057） | 看 `test_round_service.py` 与计划/轮次接口 |
| `check_token_plan.py` | Token Plan 套餐额度计费：`billing_mode='token_plan'` 按额度扣减 token、显示剩余、`cost_micros` 落 NULL（不按量折算金额），按量模式口径不变（迁移 060） | 看 `sys_ai_profile.billing_mode/token_quota` 与 `ai/cost.py` 的写入分支 |
| `check_module_tree.py` | 功能点树：三层结构、换父节点、按深度角色、能力绑定与专属提示词 | 看 `api/modules.py` 与模块树仓储 |
| `check_types.py` | 类型门禁：mypy --strict 扫全后端 + 真实执行 `vue-tsc --noEmit` + `tsc -p tsconfig.e2e.json`（Playwright 用例）；CI 可加 `--require-frontend` 让缺前端依赖直接失败 | 直接看 mypy/TS 报错的文件行号 |
| `check_noise.py` | 代码噪声门禁：TODO/FIXME 标记、debugger/console.log/alert、后端 print/breakpoint/pdb、被注释掉的代码、Python 未使用导入（尊重 `# noqa: F401`）、仓库临时文件（.tmp/.bak/.orig）零容忍 | 直接看报出的文件:行号 |
| `check_walkthrough.py` | 固定走查案例：路由顺序 + 降级顺序 + 行为安全 + 结构化记录 + 全局搜索 | 看走查各步骤断言输出 |
| `check_fix_suggestion.py` | AI 修复建议闭环：迁移与编号、Schema 校验、生成 → 采纳回写 → 终态、忽略、无模型降级、跨项目隔离（迁移 076） | 看 `services/fix_suggestion_service.py` 与修复建议接口 |
| `check_kb_vector.py` | 知识库向量检索：分块、增量索引、余弦与打包、降级、RRF 融合、项目隔离（迁移 077） | 看 `knowledge_embedding_service.py` 与嵌入配置 |
| `check_exec_media.py` | 执行媒体：落盘、大小与后缀限流、安全取回、留存清理 | 看 `services/media_service.py` 与探索性执行 |
| `check_auth_sessions.py` | 登录态复用：保存/覆盖/过期判定（cookie + JWT exp）/清除/跨项目隔离/路径穿越/角色清洗（迁移 079） | 看 `services/auth_session_service.py` |
| `check_heuristics_pack.py` | 离线测试知识包：四份字典可加载、关键词命中、能力过滤与默认分类 | 看 `prompts/heuristics/` 与 `heuristics_service.py` |
| `check_requirement_import.py` | 需求批量拆分：指纹、Schema、拆分与批次、批量采纳幂等、功能点解析、跨项目隔离（迁移 080） | 看 `services/requirement_import_service.py` |
| `check_case_insight.py` | 执行可信度与资产效率：失败归因规则、flaky 判定、用例质量体检、风险选测、导入幂等、局部修订 patch 采纳/忽略与终态、执行自检、发布就绪结论（迁移 081–083） | 看 `services/failure_attribution_service.py`、`case_patch_service.py`、`case_quality_service.py`、`risk_selection_service.py`、`execution_precheck_service.py` |
| `check_v27.py` | V2.7 平台能力：App 场景步骤（`mock_adb.py` 桩）成功/失败链路、四渠道模板渲染与邮件必填校验、通知预览、缺陷待审确认/忽略、MCP 开关/令牌生成轮换与启停删/明文仅一次/写权限/调用留痕、失败聚类、依赖防重与防环、影响面闭包（迁移 087–088） | 看 `services/app_scenario_service.py`、`notification_service.py`、`bug_draft_service.py`、`mcp_service.py`、`insight_service.py` |
| `smoke_api.py` | P3 API 冒烟：临时库跑通全部蓝图关键路径 | 按结尾「失败项」列表定位蓝图 |

通用排查：每个脚本逐条打印 `[FAIL]` 并以 `PASS=n FAIL=n` 收尾；先单跑失败脚本
`python -X utf8 backend/scripts/<脚本名>.py`，全量场景下 runner 也会打印失败脚本的输出末尾。

### CI 与保护分支（需远端手工设置）

仓库已内置 `.github/workflows/gates.yml` 与 `.workflow/gates.yml`（Gitee Go）：安装依赖、
构建前端、启动本地服务后跑 `run_all_checks.py`，并上传 JSON 报告为 artifact。
两份 workflow 在本地无法验证真实执行，以首次远端运行为准；CI 上
`check_isolation_matrix` 用 `--skip-name` 排除（长时审计，耗时且受数据时序影响），
`check_ai_live` 由 runner 永久排除。

- GitHub：仓库 Settings → Branches → 分支保护规则，勾选 Require status checks to pass，
  选择名为 `gates` 的检查（必须与 workflow 里的 job 名一致）。
- Gitee：仓库 管理 → Gitee Go 启用流水线后，在合并请求的流水线检查/必选检查中选择
  `gates`（与 `.workflow/gates.yml` 的 job 名一致），字段以 Gitee Go 官方文档为准。

---

## 端到端测试（E2E）

基于 Playwright 的全流程端到端测试，位于 `frontend/e2e/`，配置 `frontend/playwright.config.ts`。
当前 **21 个 spec / 64 条用例**，串行执行；用例代码与前端共用 strict 类型检查（`tsconfig.e2e.json`）。
工装自动拉起**隔离环境**（独立后端 8762 + 临时库、mock 模型 18790/18791、vite 5399），
绝不触碰正式 8760 与 `data/aitp.db`；teardown 树杀进程并删临时目录。

```bash
cd frontend && npx playwright install chromium   # 首次
cd frontend && npx playwright test               # 串行 workers=1
```

- **BFS**：全部路由一次访问，断言无白屏 / 无 JS 异常 / 无 5xx。
- **DFS**：核心业务页（AI 能力、用例、计划/轮次、缺陷、报告、知识库、回归）穷尽交互，每页 ≤20 元素防死循环。
- **非快乐路径**：返回/取消/放弃保存、空数据、超大量（1000 条分页+虚拟窗口）、未登录/口令、刷新/后退、表单边界（空/超长/特殊字符）。
- **问答历史不丢**（`06-assistant-history`）：走够 8 个菜单页把助手页挤出 keep-alive 缓存后回来看历史仍在，整页刷新后仍在（证明在库里而不是内存里），「清空对话」后当前线程空了但「会话记录」按「知识库问答」还能查到。
- **四期专项**（`13`–`17`）：修复建议（失败 → 生成 → 采纳回写 / 忽略终态）、向量检索（配置嵌入 → 重建 → 语义召回 + 降级）、登录态复用（导入 → 预检 → 过期拦截 → 清除）、用例脑图（切换 → 刷新还原 → 空态）、需求批量拆分（拆分 → 勾选采纳 → 重复导入跳过）。
- **执行可信度专项**（`18`）：用例质量体检（体检 → 问题明细）、执行前置自检 + 并发接口执行与失败归因、追溯页发布就绪三档结论。
- **统一附件专项**（`19`）：需求拆分仅图片、知识问答带图提问、AI 修订参考截图随请求提交、智能路由截图先转文本再匹配、AI 档案视觉开关保存与回读。
- **V2.7 平台专项**（`21`）：App 场景创建与执行（`mock_adb.py` 桩，含成功与失败断言）、通知模板预览与邮件渠道保存、缺陷待审确认与忽略、MCP 设置与 JSON-RPC 调用（`tools/list` / `tools/call` / 写工具拦截）、令牌一次性展示与轮换后旧令牌失效、质量洞察失败聚类与变更影响面。
- 每步断言业务结果，不只断言「没报错」；页面变化后重新定位元素，不复用旧 ref。
- 结果：`frontend/e2e/results.json` + 控制台 list；失败留 trace/截图。
- 约定见 `AGENTS.md`：**每次改完代码必须跑一遍验证，并把真实通过/失败结果写进回复**。

> 生产确认勾选弹窗的 UI 交互在 Playwright 自带浏览器下渲染不稳定，故 `05-prod-confirm` 用 API 级守卫断言；弹窗 UI 已在有头浏览器人工验证。

---

## 目录结构

```
backend/
  aitp/
    api/          48 个蓝图 / 511 个接口
    ai/           resolver、client、masking、adapters、cost、output_schema
    common/       常量、校验、分页、密钥库、安全工具
    db/           连接与迁移（84 份）
    repositories/ 参数化 SQL 仓储层
    services/     知识落盘、报告导出、备份恢复、执行矩阵、留存归档、探索性测试、外部导入等
    prompts/      提示词模板库
    static/       前端构建产物
  prompts/        提示词 YAML
  scripts/        门禁与初始化脚本（含 MCP / HTTP 桩服务器）
frontend/
  src/
    api/          统一请求层
    components/   通用组件
    views/        64 个页面组件
  e2e/            Playwright 端到端测试（隔离工装）
data/             运行时数据（SQLite、日志、密钥、归档、缺陷截图 bug-screenshots/、探索性截图与日志切片、执行媒体 exec-media/、登录态档案 auth-sessions/）
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

本项目采用 [Apache License 2.0](./LICENSE) 开源。