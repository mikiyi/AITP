# AITestPlatform (AITP)

English | [简体中文](README.md)

A personal AI testing workstation — **single-user, local-first, offline-capable** — a quality-control tool built around **prompt engineering and data masking for LLM calls**.

Current version **v2.7.0**: the same value served by `aitp.__version__`, `db/migrator.py::APP_VERSION`,
the `appVersion` field of `GET /api/v1/health` and the `app_version` recorded by each migration.

It folds the AI lab, test cases, defects, log analysis, SQL analysis, regression, reports, knowledge base, task list and workspace configuration into one closed loop of LLM-assisted testing:
**requirement → test point → test case → execution → defect → regression → report → knowledge retention**, with todos moving the progress along at every step.

Once this loop turns, assets snowball on their own — the more you use it, the better the AI knows your project:

**AI output → adopted as assets → executed with evidence → distilled into knowledge → fed back into the next generation → …**

On day one the AI knows nothing about your project and its drafts can be generic; after a while things change:

1. **Cases and scripts the AI produced** are adopted into your asset library with one click, managed alongside hand-built ones;
2. **Adopted assets get executed** — API cases and automation are really run, manual cases get recorded results — what passed and what failed is on the record;
3. **Pitfalls and verified conclusions** go "add to knowledge" and, after review, become project knowledge;
4. **On the next generation run** related cases, defects and knowledge are automatically loaded into the prompt — the AI knows where the feature points live, what defects hit before, and which pitfalls you stepped in.

The library gets sharper with use, and every output fits your project better — that is the asset flywheel.

- All data lives in a local SQLite file (`data/aitp.db`), bound to this machine only — never exposed to the LAN or the internet.
- AI settings are read from local environment variables, from in-app configuration, and from local models (Ollama).
- When there is no cloud model, the model is unavailable, or the network is down, AITP degrades to local rules automatically and never blocks the main workflow.

## Eval subsystem preview (v0.1, Nov 2026) — now it can also test AI software

AITP used to answer "test software with AI". This release adds the other half: **evaluating AI software**.

- **Schema**: migration `089_eval_core.sql` with ten tables — suite / case / config / judge / run / result / metric / calibration / gate / gate result
- **Pure domain layer**: `backend/aitp/eval/` (metrics, assertions, gate evaluation; no IO, unit-testable)
  - Metrics carry **95% Wilson intervals**; `min` gates use the **lower bound**, `max` gates use the **upper bound**
  - Severity weighting S0/S1/S2 = 50/10/1: one "you can buy it" when the client can't ≈ 50 formatting misses
  - Sample tiers: n<30 evidence only, n<200 warn only, n>=200 may pass/fail
- **Runs**: `eval_service.execute_replay()` scores supplied outputs, then persists results, metrics, gate conclusion and a Markdown report
- **API**: 12 routes under `/api/v1/evals`
- **Gates & demo**: `check_eval_metrics.py` (6/6), `eval_demo.py` (12 finance cases, good → pass, bad → fail)
- **Cross-check toolkit**: `tools/eval_toolkit/` runs the same cases through Promptfoo with **24/24 agreement**; a minimal Ragas demo shows faithfulness attribution

```bash
python backend/scripts/check_eval_metrics.py
python backend/scripts/eval_demo.py
python tools/eval_toolkit/build_config.py
```

**Data boundary**: the engine is open source, the evaluation corpus is not. Only public-rule demo cases ship in this repository — no real business samples or customer data.

Design notes: [EVAL.md](EVAL.md).

## v2.7.0 changes (closing the last execution path + platform outreach)

- **App scenario engine**: scenario automation now covers its last execution path — the App tab is natively usable. Steps support launch / stop / tap / input / swipe / back / home / wait / assertText / screenshot, reuse the adb driver and the unified evidence contract; text assertions read the real UI through `uiautomator dump`, screenshots land in `data/app-shots/`; the adb command, device serial and command timeout are configurable on the page with a "test connection" probe that lists online devices
- **Notification templates (configurable channels)**: channels gain a type and a template — Webhook / DingTalk / WeCom / Feishu / email share `{{eventLabel}}`, `{{title}}`, `{{projectName}}`, `{{occurredAt}}` and more, with a title + body template, one-click variable insertion and a rendered preview; email goes over SMTP with the password in the local secret store (the API only returns `hasPassword`), and test sends use the same render path as real alerts
- **Automation failure → semi-automatic defect flow**: new `automation.auto_bug_mode` with three modes — off / review (default) / auto, switched by a radio control on the performance test settings page (saving mirrors the legacy boolean key, and the backend accepts only the three enum values, rejecting anything else at the entry point). In review mode scenario and automation failures are first written as defect drafts; the defect list shows a pending area with source and actual result, and only a human "confirm as defect" creates the real defect with linked cases, while "ignore" is terminal
- **AITP MCP server (read-only first)**: `POST /api/v1/mcp` speaks JSON-RPC 2.0 (`initialize` / `tools/list` / `tools/call` / `ping`); read-only tools cover projects, cases, requirements, runs, defects and quality insights, the write tool `bugs.draft.create` only works when write access is explicitly enabled and only writes a pending draft; off by default, loopback only, optional Bearer token and per-call logging; the plaintext token is shown once on generate/rotate, and history tokens can be disabled, re-enabled or deleted with last-used/client tracking
- **Cross-run failure clustering**: failures from the recent N runs are clustered by a normalized signature of title + actual result, with occurrence counts, involved cases, source runs, first/last seen and per-run trend, separating repeated breakage from one-off noise
- **Case dependencies and change impact**: maintain case-to-case dependencies (same project, no self-dependency, no cycles); after picking changed cases and requirements, the impact set is computed from direct changes + requirement links + reverse-dependency BFS, annotated with the recent failure rate
- **Quality engineering**: migrations 087 (notification template fields, defect drafts, case dependencies, MCP call log and defaults) and 088 (MCP token hashing, rotation and enable/disable history), the `check_v27.py` gate and E2E spec 21; endpoints grow to 511 / 48 blueprints and the version becomes v2.7.0

## v2.6.0 changes (execution trust + asset efficiency)

- **Failure attribution**: failed interface executions are automatically classified as "suspected bug / assertion error / environment issue / data issue"; the rules are explainable and stored with the evidence. Environment and data failures are intercepted at the fix-suggestion entry so infrastructure problems are not written up as assertion edits
- **flaky detection and stability retries**: failures can be retried 0–5 times; a pass after retry, or flipped results within the last five runs, marks the case "unstable". Reports and release verdicts list flaky separately from real failures, reducing false alarms caused by environment jitter
- **Case local-diff revision**: select a batch of cases and type one instruction (add exception scenarios / change expectations / adjust priority / remove duplicates); AI returns a per-case patch, and nothing is written back until a human adopts it, with an audit trail. Without a model it says so clearly and never blocks manual maintenance
- **Release readiness verdict**: the release gate page now gives a three-tier verdict — "ready / ready with risk / blocked" — aggregating P0 failures, open defects, coverage gaps, flaky cases and environment-class failures, with actionable recommendations
- **Pre-execution self-check**: before a batch run it checks case scope, environment URL, login state, interface URL/method and assertion coverage; missing items block or warn up front, avoiding a batch of failures with no diagnostic value
- **Concurrent execution and isolation**: interface batch runs support 1–8 workers — requests run in parallel, writes stay serial; one bad case never breaks the batch, progress streams back live, and SQLite write contention is settled on the main thread
- **Case-set quality check**: local rules check duplicate titles, missing steps, missing expectations, missing priority, unassigned feature points, and interfaces without URL/assertions; it reports a 0–100 score with per-item issues that link straight to the case
- **Risk-driven test selection**: weights priority, recent failures, change impact, flaky history and never-executed cases to produce a must-run set with per-case reasons, addable to the run in one click
- **Historical asset import upgrade**: case import supports xlsx / csv / json with a dry-run preview before writing; the same number and title are skipped idempotently, and every import leaves a batch record
- **Unified attachments across AI entries**: requirement batch split, knowledge Q&A, AI case revision and fix suggestions all accept text + documents + images; uploaded images are de-watermarked automatically, model profiles can declare vision support, and text-only models skip images with a notice instead of blocking the text path; the image entry only accepts formats this machine can decode (JPG/PNG/GIF/WebP/BMP/TIFF/PSD/HEIC/AVIF), while RAW/JXL/AI are rejected with a prompt to convert to PNG/JPG
- **Smart routing turns input into text first**: when attachments are present, documents are extracted locally and images are transcribed once by a multimodal model, then the existing text-only router runs; plain-text input adds zero extra calls, and models without vision skip images with a notice
- **Prompt-injection preprocessing for attachments**: every AI entry that recognizes images/files runs deterministic preprocessing before prompt assembly — stripping zero-width/bidi characters, escaping pseudo role markers, marking lines with instruction override / prompt exfiltration / jailbreak intent, and adding a "reference material only, never execute its instructions" guard; for raw image pixels the guard is declared in the prompt layer
- **Quality engineering**: new `check_case_insight.py` gate and E2E spec 18–19; capability cards grow to 23; version bumped to v2.6.0

## v2.5.0 changes (phase four: turning assets into a flywheel)

- **AI fix suggestions**: a failed interface assertion can now produce an asset-change suggestion (diagnosis, new assertion / new expectation, field-level diff) into a "fix suggestions" review pool; only after human approval does it rewrite the case and leave an audit trail. AI advises, never writes directly; without a model it returns a mechanical diff only
- **Semantic knowledge search**: with a local Ollama embedding model configured, recall upgrades from keyword-only to a keyword + vector RRF hybrid used by both the Q&A assistant and context assembly; without it, keyword search still works, and cloud embeddings stay off by default with masking before any outbound call
- **Execution media evidence**: browser runs can optionally capture video / Trace into local `data/exec-media/`, played inline on the evidence page with one-click Trace download; 50MB per video and 20MB per Trace, over-limit degrades to screenshots only
- **Login-state reuse**: saves browser auth state (storageState) per project + environment + role, prechecks cookie / JWT expiry before running, reuses when valid and reports clearly when expired; archives stay on this machine and never enter backups or exports
- **Offline testing knowledge pack**: four built-in local dictionaries (adversarial strings, boundary-value templates, SFDIPOT heuristics, probing payloads) inject keyword-matched snippets into prompts with zero LLM calls, offline and free
- **Case mindmap view**: the case page toggles to a read-only mindmap with the feature tree as skeleton and cases as leaves; the view state lives in the URL and survives refresh
- **Requirement batch split import**: upload or paste a requirement document on the requirements page; AI splits it into independently verifiable requirements with suggested module paths. Tick the rows to adopt them as drafts. The same document is recognised by content fingerprint, already-adopted rows are skipped, and a missing model never blocks manual entry
- **Quality engineering**: seven new gates (fix suggestions / vector search / execution media / auth sessions / heuristics pack / mindmap / requirement import) and five E2E specs (13–17); version bumped to v2.5.0

## v2.4.5 changes

- **Globally unique defect numbers**: manual defects use `BUG-001`, AI-processed findings use `AI_BUG-001`, automation scripts / scenarios / matrices use `Auto_BUG-001`, each sequence incrementing on its own; numbers are globally unique and never reused after soft delete (so screenshot filenames are never overwritten); cross-project duplicates in legacy databases are shifted behind the current maximum during the startup migration
- **Defect screenshots**: the defect detail page supports uploading, previewing, downloading and deleting screenshots; files live in `data/bug-screenshots/<bugNo>/` named `pic_<bugNo>_001.png` and increment in order; 10MB per file, 30 per defect, bitmap formats only (PNG/JPEG/GIF/WebP/BMP)
- **Backup switch**: "Settings -> Data backup & restore -> Backup content" adds "Include defect screenshots", off by default; when on, full bundles, project asset packages and retention archives all include screenshots, and when off all three exclude them (files stay on this machine and are excluded from retention cleanup)
- **Quality engineering**: new `check_bug_screenshots.py` gate and E2E spec 12 (number prefixes, global uniqueness, no reuse, screenshot naming/limits/deletion, all three switch targets)

## v2.4.4 changes

- **AI case-output export**: cases not yet imported get a `草稿-001` draft number in the "case number" column; adopting or importing assigns the official number, and draft numbers never land in the database
- **Automation scripts**: new scripts belong to the current project with a default framework and starter content; category stats are project-scoped; schedule targets come from scenario/script dropdowns; execution and batch execution are de-duplicated; edits accept a change note
- **Scenario and database-script forms** carry inline variable-usage hints; copied scenarios count as references; schedule "next run" shows local time
- **Quality engineering**: E2E specs join the type gate (`tsconfig.e2e.json`); a code-noise gate was added; dead code and duplicated frontend scaffolding were cleaned up

## Running demo

Both demos below run on a **local Ollama mock model** (`docs/screenshots/` holds real screenshots, not mockups):

**AI capability cards · test-case generation** — pick a capability card → fill in the requirement → the local model produces structured test cases (positive / exception / boundary / interface), exportable to Excel/Word or adopted as cases in one click:

![AI capability test-case generation demo](https://gitee.com/mikiyi/AITP/raw/master/docs/screenshots/demo-ai-capability.gif)

**Knowledge-base Q&A assistant** — enter a knowledge item into the review pool → ask a question → the assistant searches the knowledge base first, then lets the model synthesize, with `[K1]`-style citation markers:

![Knowledge-base Q&A assistant demo](https://gitee.com/mikiyi/AITP/raw/master/docs/screenshots/demo-knowledge-assistant.gif)

> The demo uses a local mock model as the default profile and consumes no cloud quota; point it at a cloud key and the same pipeline calls a real LLM.

**Home dashboard** — data overview, knowledge-base Q&A entry, customizable cards:

![Home dashboard](https://gitee.com/mikiyi/AITP/raw/master/docs/screenshots/01-dashboard.png)

**Test cases** — browse by function-point tree, filter, import/export, coverage matrix:

![Test case list](https://gitee.com/mikiyi/AITP/raw/master/docs/screenshots/02-cases.png)

---

## Quick start

**Windows**

```bat
start.bat
```

**macOS / Linux**

```bash
./start.sh
```

The first run creates `.venv`, installs dependencies, builds the frontend, then serves at <http://127.0.0.1:8760>.

Stopping the service:

```bat
stop.bat          :: Windows
```

```bash
./stop.sh         # macOS / Linux
```

### Launcher flags

| Flag | Description |
|---|---|
| `--build` | Force a frontend rebuild |
| `--reinstall` | Recreate the virtualenv and reinstall dependencies |
| `--no-browser` | Do not open the browser on startup |

The launcher checks the port first: if AITP is already running it just opens the page; if the port is taken by another program it exits with a clear error.

### Requirements

- Python 3.11+ (3.11 / 3.12 are fully gate-verified; 3.13 works, numpy switches to 2.x automatically)
- SQLite 3.34+ with FTS5 trigram (self-checked at startup; an outdated system Python triggers an interpreter hint)
- Node.js 20+ (only needed for the first frontend build)

---

## Feature map

Frontend: 59 routes / 64 page components. Backend: 48 blueprints / 511 endpoints, all under `/api/v1`.

### First-launch tour and demo project

- On first launch with an empty database, the home page automatically opens a four-step tour: configure an AI model → load the demo project → run an AI generation → view reports and traceability. It can be skipped; once completed or skipped it no longer auto-opens, and upgrading an existing database does not disturb the user by default.
- "Load demo project" appends the `DEMO-EC` sample (requirements / cases / defects / knowledge / reports + the executable API assertion script `AUTO-API-001`). It rebuilds only the demo itself, never touches existing data, and can be clicked repeatedly to rebuild; the empty project list also offers one-click loading.
- "Reopen first-launch tour" at the top-right of the help page (`/help`) replays it anytime; state endpoint `GET/POST /api/v1/onboarding`, injection endpoint `POST /api/v1/onboarding/demo`.

### Home

- Overview metrics: projects, test cases, defects, knowledge awaiting review
- Quick access: top 10 entries by real click count (including AI capabilities & module tree, assets, system settings); never-clicked entries fill remaining slots in original order
- Recent reports and defect activity
- Customizable cards: see **Settings → Home card customization** (`/settings/home-cards`)

### Appearance and navigation

- **10 themes**: Light, Dark, Blue, Red, Pink, Orange, Tech, Ocean, Botanical, Industrial. One click on the palette in the top-right applies the theme site-wide.
- **Navigation groups**: the sidebar groups by purpose into 10 groups — AI Lab / Task Center / Test Plans / Test Assets / Quality Conclusions / Automation Scripts / Execution & Exploration / Knowledge Base / Data Governance / Settings, plus Home and Help as direct entries; entering a page auto-expands its group, and both manually opened/closed groups and the collapsed state are remembered locally.
- **Filters are queries**: changing a filter runs the query immediately — no "Search" button. Filters and pagination are kept in the URL, so links are shareable.

### Task list

Page: **Task list** (`/todos`)

- Top summary: open / due today / overdue / completed / completion rate
- Left views: all tasks, due today, due this week, overdue, pinned, completed
- Middle list: quick entry, filtering, bulk actions, export
- Right detail: status, priority, due date, category, tags, project, progress and notes, with subtasks
- Subtask completion automatically rolls up to the parent task

### AI lab

23 capabilities. Knowledge-base Q&A runs through the "Knowledge Base → Q&A assistant" chat panel:

| Capability | ID |
|---|---|
| Requirement confirmation | `requirement_confirm` |
| Test point mind map | `testpoint_generation` |
| Test case generation | `case_generation` |
| XMind to test cases | `xmind_to_case` |
| Defect analysis | `bug_analysis` |
| Log triage | `log_analysis` |
| SQL analysis | `sql_analysis` |
| Regression checklist | `regression_generation` |
| Report generation | `report_generation` |
| Progress board | `project_progress_board` |
| API test design | `api_test_design` |
| Test plan | `test_plan` |
| Test case review | `case_review` |
| Defect classification | `bug_classification` |
| Risk assessment | `risk_assessment` |
| Automation script generation | `automation_script` |
| Performance script generation | `performance_script` |
| Test data generation | `test_data_generation` |
| Web exploratory testing | `exploratory_web` |
| Prompt optimization | `prompt_optimize` |
| Fix suggestion | `fix_suggestion` |
| Requirement batch split | `requirement_import` |
| Case local-diff revision | `case_patch` |

- **Capability cards are driven by the prompt library**
- **One-click output import**: the SQL data-generation statement can be added as a database script in one click, and automation-script generation adds a web/app/API automation script in one click by produced code type; imports carry an automatic `AI_`-prefixed number into the matching menu and the badge updates in real time
- **Smart routing**: type one sentence and the best-matching capability is selected automatically
- **Pre-flight check**: before running, it lists missing items, completeness, primary/auxiliary tasks and execution order
- **Context assembly**: modules, existing cases, defects and knowledge are recalled per project/feature point and injected into the prompt; tiers are lite / standard / full, defaulting to standard (GAP-27)
- **Output schema validation**: results are validated against a JSON Schema; missing or mistyped fields are blocked on the spot with a retryable Chinese message (GAP-28)
- **Generation de-duplication**: new output is compared against cases already in the library, in "mark suspected" or "incremental against baseline" mode, and suspected items are unchecked by default (GAP-29)
- **Cost accounting**: per-profile unit prices convert every call into tokens and money; daily/monthly budgets and per-call token limits are soft warnings that never block (GAP-30); self-hosted (local) models incur no cloud spend and are never billed
- **Disable thinking mode**: a per-profile switch that lets reasoning models such as qwen3 / DeepSeek-R1 skip the thinking phase and answer directly (measured 57s -> 13s for in-product Q&A on a local qwen3:8b); the field sent downstream follows the protocol automatically (Ollama native `think=false`; OpenAI-compatible `reasoning_effort=none` plus `chat_template_kwargs.enable_thinking=false`)
- **Session records**: every run leaves a trace — input, output, model, usage and degradation
- **Streaming output**: the request returns a task id immediately and the frontend polls `framesUrl` for incremental frames (long connections no longer occupy sync workers); if the primary model fails, a degradation notice appears before switching models

### Test assets

| Module | Capabilities |
|---|---|
| Projects | CRUD, current-project switching, status management |
| Requirements | Maintenance, direct feature-point linking (module / sub-module / feature), bulk delete, export |
| Test cases | CRUD, status transitions, xlsx import/export, bulk execution |
| Defects | CRUD, state-machine transitions, linked test cases, log attachments, drill-down into original execution evidence; globally unique numbers with source prefixes (manual `BUG-`, AI `AI_BUG-`, automation `Auto_BUG-`, never reused after soft delete); upload / preview / download / delete screenshots on the detail page (`data/bug-screenshots/<bugNo>/`, `pic_<no>_001` incrementing, 10MB per file, 30 per defect); scenario and automation failures follow the off / review (default) / auto switch — review mode first writes a "defect draft" shown in a pending area on the defect list, and only human confirmation creates the real defect with linked cases, while ignore is terminal |
| Assertion library | Reusable assertion rules shared per project/feature point, referenced from interface cases as `lib:<code>`, JSON Schema assertions with failure diffs |
| Database query checkpoints | Interface assertions gain a `db:` target (`db:` compares the first cell, `db:rows:` the row count, `db:json:` the result JSON) and scenarios gain a "database" step; connections come from the execution environment (passwords via the OS secret store), read-only enforced (writes / multi-statements / INTO rejected), timeout and 200-row cap, `{{var}}` parameterization; covers MySQL/PostgreSQL-protocol domestic databases (OceanBase/TiDB/GBase/KingbaseES/Highgo/openGauss/GaussDB/OSCAR), Dameng, ClickHouse, SQLite, MongoDB and Redis with lazy drivers and install hints when missing; query text and results land in the unified evidence |
| Knowledge base | Pending pool → official library, versioning, review, reference protection, full-text search |
| Test reports | Generation/adoption, ECharts charts, metric trends, export |
| Execution and evidence | Interface / automation / manual / regression / **exploratory** runs each write one unified evidence snapshot, drillable from runs, defects and reports |
| Regression checklist | CRUD, AI-generated regression items, auto items really executed by the server with evidence, manual items require evidence to pass |
| Scenario automation | No-code multi-step scenarios organised in **Web / App / API** tabs (App native steps are now wired in: launch / stop / tap / input / swipe / back / home / wait / assertText / screenshot, adb-driven, text assertions read the real UI via uiautomator dump, screenshots land in `data/app-shots/`; adb command, device serial and command timeout are configured on the page with a "test connection" probe); steps can be interface / browser / database / **automation script**, each reusing the interface assertion engine (an automation-script step references a Web/App/API automation script by number and runs it with the scenario, its run record landing in the unified evidence — reusing the automation engine instead of a second execution wheel): JSONPath extraction feeds `{{var}}` into later steps (database-step query results are extractable too), a failed step can stop or continue the run, the whole scenario can be driven record by record from a data pool, and every run is kept in history. Scenarios carry an SCN number and an enabled/disabled status; the list supports name/number keyword and status filters, and the status can be set in the create/edit form. Naming: scripts = Python code (Web / App / API automation scripts with AUTO-W/AUTO-A/AUTO-I prefixes, PERF for performance, SQL for database scripts; AI one-click imports use the separate **AI_SQL-xxx** sequence for database scripts and **AI_AUTO-W/AI_AUTO-A/AI_AUTO-I/AI_PERF-xxx** for automation scripts, so the source is obvious at a glance), scenarios = no-code orchestration; schedules carry SCH numbers. A script is "in use" only when referenced by an automation step inside a scenario (the hover shows the referencing scenario SCN numbers; schedules and plain execution history do not count): hovering the button shows the referencing numbers as an at-most-10 item enumeration, with a "view more" dialog listing every reference beyond that; copied scenarios also count as references. Database-script and scenario-step forms carry inline variable-usage hints with {{var}} examples; new automation scripts start with a runnable default framework and starter content; category stats are scoped to the current project; schedule targets are picked from scenario/script dropdowns |
| AI one-click import | AI-lab output turns into assets directly: the SQL data-generation statement can be added as a database script in one click, and automation-script generation adds a web/app/API automation script in one click according to the produced code type; the import carries an automatic `AI_`-prefixed number into the matching menu and the badge updates in real time. When case-type output is exported to Excel, cases not yet imported get a `草稿-001` draft number; adopting or importing them assigns the official number (draft numbers never land in the database) |
| Standalone data pool | One shared test-data set reused by cases and scenarios: unique name per project, parse preview, referenced as `pool:<name>` from case extension fields and scenario data sources, parsed by the same case test-data engine instead of a second fetch path |
| Execution matrix (GAP-43) | Fan a batch of cases across multiple environments × endpoints (app servers), dispatch once and really run each cell; per-case cross-cell attribution (common pass / common failure / env-specific / endpoint-specific / mixed); one-click defect conversion for common failures with evidence attached; production environments require explicit confirmation to dispatch |
| Quality insights | Cross-run failure clustering: failures from the recent N runs are clustered by a normalized title + actual-result signature with counts, involved cases, source runs, first/last seen and per-run trend; change impact: maintain case dependencies (same project, no self-dependency, no cycles) and compute the affected set from direct changes + requirement links + reverse-dependency BFS, annotated with the failure rate over the last 20 runs |
| Web exploratory testing (GAP-45) | An MCP-driven real browser (started by the `mcpCommand` you configure under "Exploratory testing settings"; `backend/scripts/browser_mcp_server.py`) explores in a read-snapshot → decide → act → observe loop, finding gaps that scripted cases miss; normal steps keep evidence at the configured per-step screenshot count, anomalous steps force 3 screenshots plus a dual-anchor ±N-second log slice, network snapshot, console errors and traceId as an evidence pack; password/token inputs are masked per PII rules; findings go to the pending pool for human review first and only become defects after approval |
| AITP MCP server | `POST /api/v1/mcp` (JSON-RPC 2.0: initialize / tools/list / tools/call / ping), off by default, loopback only, optional Bearer token; read-only tools cover projects / cases / requirements / runs / defects / quality insights, the write tool `bugs.draft.create` requires explicit write access and only writes a pending draft; the plaintext token is shown once on generate/rotate, while history keeps the mask, status, last-used time and client with disable/re-enable/delete; every call is logged in `sys_mcp_call_log` |
| Knowledge / report fragment reuse (GAP-42) | A fragment is maintained once in its source project; target reports register a reference and rendering / export reads the body live, avoiding drifting copies; referenced fragments cannot be deleted |
| Data retention and archival (GAP-44) | Per-data-type retention days and expiry actions (archive / soft-delete / summary-only) for runs, automation runs, evidence, logs, todos and defect screenshots (screenshots only when the backup switch is on); reference protection (bug-linked evidence, matrix-linked records survive expiry); expired data is zipped into `data/archive/` and searchable with original readback; dry-run preview and startup auto-cleanup supported |

### Workspace configuration

| Page | Contents |
|---|---|
| AI configuration | Provider profile CRUD, API keys in the OS keystore, priority ordering, real self-check, model list, profile unit prices and call cost, context tier and de-duplication mode, soft budget thresholds |
| Knowledge base settings | Storage directory, file mode, rebuild index on startup |
| Module tree | Three-level structure (a child's level role follows its parent: module -> sub-module -> feature), capability binding and per-node prompt |
| System settings | Read/write all configuration items |
| Test environment configuration | Multiple named environments, credentials in the system keystore, reachability probe |
| Data backup | Database snapshot backup, full-bundle backup, restore; an "Include defect screenshots" switch (off by default) controls whether full bundles / project asset packages / retention archives carry screenshots |
| Data retention and archival | Per-type retention days and expiry actions, expiry preview, dry-run, run cleanup now, archive search and original readback (GAP-44) |
| Exploratory testing settings | Max steps / duration / per-step timeout / per-step screenshot count (anomalous steps force 3 more), record selectors, input masking toggle, log-slice window and sources, MCP server command and pinned-tool allowlist (GAP-45) |
| Project asset package | Structure-package export / preview / rename-on-import: modules, versions, requirements, cases, defects, knowledge, scripts, assertion rules; every id is reallocated and foreign keys remapped; keystores and execution history never leave the package |
| External platform import | Read-only one-way import from Zentao / Jira / TestLink: after login-token exchange only GET requests are sent and nothing is ever written back; Zentao and Jira import requirements / cases / defects, TestLink imports requirements / cases; credentials go to the system keystore, with two-layer "platform account + server-level HTTP Basic" auth; mapping templates are editable, dedup is per project + platform + external id, and every batch is recorded |
| CI and notifications | CI tokens, notification channels (Webhook / DingTalk / WeCom / Feishu / email), channel type + title/body templates with variable insertion and rendered preview, email SMTP (password in the system keystore, the API only returns `hasPassword`), event subscriptions, test send and send history |
| MCP server | `POST /api/v1/mcp` switch, allow-write switch, endpoint and token status (plaintext shown once on generate/rotate), token history (disable/re-enable/delete, created and last-used time, last client), tool inventory and recent call log |
| Home card customization | Show/hide and reorder non-fixed home cards |

---

## AI integration

Three-level priority: **workspace configuration (database) > environment variables > built-in defaults**

1. **Environment variables** (work out of the box)
   - Generic: `AITP_AI_PROVIDER` / `AITP_AI_BASE_URL` / `AITP_AI_API_KEY` / `AITP_AI_MODEL`
   - Per provider: `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` / `SENSENOVA_API_KEY` / `MINIMAX_API_KEY` / `BIGMODEL_API_KEY` / `XIAOMI_API_KEY` / `MOONSHOT_API_KEY` / `ARK_API_KEY` / `HUNYUAN_API_KEY` / `SILICONFLOW_API_KEY` / `OPENROUTER_API_KEY` / `GITEE_AI_API_KEY` / `OLLAMA_HOST` …
2. **Workspace configuration**: add provider profiles on the AI configuration page and set their priorities
3. **Local models**: Ollama (`ollama_native` protocol)

Built-in providers (just fill in a key): OpenAI, DashScope (Qwen), DeepSeek, SenseNova, **MiniMax (CN / Global)**, **Zhipu BigModel**, **Xiaomi MiMo**, **Kimi by Moonshot (CN / Global)**, **Volcano Engine Ark (Doubao)**, **Tencent Cloud Hunyuan**, **SiliconFlow**, **OpenRouter**, **MoArk (Gitee AI)**, plus a custom compatible endpoint; Ollama for local models.

- **Access mode (shown per provider)**: switch a provider between **pay-as-you-go API** and **subscription plans (Agent / Coding / Token Plan)**; choosing a plan auto-fills the matching endpoint, protocol and key-format hint (e.g. Xiaomi `token-plan-cn.*` + `tp-/ttp-`; MiniMax / Kimi coding plans use Anthropic-compatible endpoints).
- **Protocols**: OpenAI-compatible (`openai_compat`), Anthropic Messages-compatible (`anthropic`, covering the official API and vendor coding plans), Ollama native (`ollama_native`).

### Priority and automatic degradation

- Local rules respond instantly; AI calls are subject to model latency and a timeout, **60 seconds** by default
- Local and cloud models share **one ordered list** and are tried from highest priority down
- If the primary model is unavailable, AITP degrades to the next candidate; the frontend shows a **3-second** toast
- If no candidate works, it degrades to **local rules**
- **Self-check is a real call**: it returns ✓/✗, latency, a reply snippet and token usage

### Token Plan (subscription quota) mode

Besides **pay-per-use API**, an AI profile can use **Token Plan** (`billing_mode=token_plan`, migration 060):

- A prepaid token quota (`token_quota`) is consumed via the same OpenAI-compatible endpoint with the plan credential; the wire protocol and auth are unchanged.
- Each call deducts `totalTokens` from the quota (`token_used`); the UI shows remaining = quota − used.
- Token Plan **does not bill money per use** (`cost_micros` stays NULL), consistent with the "unpriced reports tokens only" rule.
- `token_quota=0` means no quota set (statistics only, no deduction); deduction uses SQL self-increment so concurrent calls never lose counts.

---

## Data and knowledge governance

- Official knowledge entries are persisted as **Markdown** (`knowledge/`); SQLite stores only the index and metadata
- **Pending pool**: AI output lands there first and only becomes official after human review
- **Q&A assistant, three modes**:
  - `llm`: answers from the model's own knowledge (sends data out)
  - `kb`: local retrieval only, returns verbatim excerpts (no egress)
  - `hybrid` (default): retrieves the knowledge base first and feeds matched text tagged `[K1]…` to the model for consolidation (sends data out)
- **Q&A history is stored per session**: every question and answer is written to a session (capability `knowledge-assistant`), so switching menus, reloading or reopening the browser replays the thread; "clear conversation" starts a new session while the old one stays in the database and remains reachable under "AI records -> Session records" filtered by capability; the replay carries the answer mode, references and archived timestamp, so clicking "add to knowledge base" again is detected as a duplicate instead of entering the pool twice; Q&A sessions write no AI execution record and stay out of cost and budget accounting

---

## Performance and reliability

- **Lists never dump whole tables**: list APIs paginate server-side (`pageNum` / `pageSize`, max 100 per page) and the case tree continues by `afterId` cursor; tables switch to a virtual window past a row threshold so only viewport rows render
- **Long work leaves the request thread**: bulk validation run-all, large exports and AI streaming run as background tasks — the API returns a task id and progress/results are polled
- **Write hardening**: in-process write serialization with bounded `SQLITE_BUSY` retries; generated business numbers are claimed inside the same transaction so concurrent creates cannot collide on unique keys
- **Observability**: every request carries a `traceId` through response headers and structured logs; logs record only errors and slow requests; `/api/v1/health` reports real database latency, write queue and AI configuration state
- **No silent failures**: a caught exception is either logged with a `traceId` and continued, or converted into a wrapped error code — never a bare `pass`; the frontend shows a visible error with a retry entry instead of a blank screen

---

## Security boundaries

- **Local only**: binds `127.0.0.1` by default
- **Same-origin protection**: write endpoints must come from a local page
- **Local access passcode (optional)**: enable it under Settings → Access passcode; it expires after 12 hours
- **Secret storage**: API keys live in the OS keystore (Windows DPAPI / macOS Keychain / Linux Fernet)
- **Masking on AI egress**: cloud calls automatically replace phone numbers, ID numbers, emails, bank card numbers, API keys and more
- **AI behavior safety**: states insufficient evidence instead of inventing it; never fabricates knowledge titles or defect IDs; AI never approves a release; P0/P1 and prompt test results always require human review
- **Unified production confirmation**: any run / interface batch / automation / execution matrix touching a production environment opens a single "I acknowledge the risk" checkbox confirmation (unchecked by default, cannot execute until checked); the backend also enforces `confirmProd` — a double UI + API guard
- **Terminal-state locking**: published orchestrations lock edit/delete; published/archived reports are terminal and archived cannot revert to draft; completed rounds freeze results — finalized assets cannot be silently changed back

---

## Quality gates

One command runs every deterministic gate (the runner auto-discovers `backend/scripts/check_*.py`
plus `smoke_api.py`, executes them in lexicographic order, prints a summary table, and exits
non-zero on any failure):

```bash
python backend/scripts/run_all_checks.py                                  # full run, 300s default per-script timeout
python backend/scripts/run_all_checks.py --list                           # list scripts that would run
python backend/scripts/run_all_checks.py --only check_db,check_fts        # substring filter, comma-separated
python backend/scripts/run_all_checks.py --skip-name check_walkthrough --timeout 600
python backend/scripts/run_all_checks.py --json logs/gates.json           # JSON report for CI archiving
cd frontend && npm run build                                              # frontend gate (vue-tsc --noEmit && vite build)
```

- The runner probes `http://127.0.0.1:8760/api/v1/health` first (override with `--base-url` or
  `AITP_BASE_URL`). If the probe fails it warns explicitly and marks service-dependent scripts
  (`check_isolation_matrix`) as FAIL with "start the local service first"
  (`python backend/run.py`). It never auto-starts the service and never pretends to pass.
- `check_ai_live` is permanently excluded by the runner: it calls the real SenseTime model API,
  burns quota and is non-deterministic; run it manually when needed.
- `--isolated`: no need to start the service first. The runner takes a consistent snapshot of the
  live database through the SQLite backup API (read-only access to the real file), boots a
  throwaway service on a free port with its database and log in a temp directory, and injects
  `AITP_BASE_URL` plus those paths into every child process. On exit the runner kills the process
  tree and deletes the temp directory. The secret key and prompts still point at the real paths,
  otherwise encrypted columns in the snapshot cannot be decrypted; the flag is mutually exclusive
  with `--base-url` and is not what CI needs (a clean runner has no live database to snapshot).
  The measured reason to use it: without the flag, HTTP-driven gates such as `check_isolation_matrix`
  leave soft-deleted rows in the live database (a full suite added +2/+7/+4/+6 rows, all `is_deleted=1`);
  with it the same gate reports `PASS=191 FAIL=0` and the live database delta is zero.
- Any new gate script dropped into `backend/scripts` named `check_*.py` is picked up
  automatically without touching the runner.

Gate inventory (83 entries = 82 `check_*.py` scripts + `smoke_api.py`, one-to-one with the real
scripts under `backend/scripts`; `check_ai_live` is not run by the runner, so 82 items actually
execute):

| Script | Covers | When it fails, look at |
| --- | --- | --- |
| `check_ai_live.py` | Live SenseTime model full-path test (**never in the runner**: real calls, burns quota, non-deterministic) | run it manually; see env vars in the script header |
| `check_ai_masking.py` | AI outbound masking (phone / ID / email / bank card / API key) | `backend/aitp/ai/masking` rules and profile config |
| `check_ai_thinking.py` | "Disable thinking mode" end to end: `extra_json.thinking=off` -> resolver -> request body of both adapters; nothing is sent when the flag is unset (default semantics unchanged) | `ai/resolver.py::_disable_thinking` and `_body` in `ai/adapters/*` |
| `check_ai_cost.py` | AI cost accounting: per-profile unit prices and micro-unit conversion, daily/monthly budgets and per-call token soft threshold, over-budget only warns and never blocks (GAP-30) | `backend/aitp/ai/cost.py` and the price fields of `sys_ai_profile` |
| `check_ai_local_billing.py` | Self-hosted (local) profiles are never billed: write side zeroes out, the unit-price endpoint refuses, cost table and currency are not skewed by leftover values, real runs store NULL cost | `cost.is_local_profile`, plus billing handling in `api/settings.py` and `api/lab.py` |
| `check_api_scenario.py` | Multi-step API scenarios: sequential runs, JSONPath variable extraction with `{{var}}` handed to later steps, per-step assertions reusing the interface-runner rules, fail-fast, data-driven execution of the whole scenario per record, run history persisted, scenario automation steps referencing and running automation scripts (ships its own `/login` `/order` stub) | `services/api_scenario_service.py` and `api/api_scenarios.py` |
| `check_egress_guard.py` | The outbound-data confirmation looks only at the primary model actually used: local primary stays silent, cloud primary prompts, the candidate chain is still returned in full | `knowledge_assistant_service.egress_plan` and frontend `stores/aiGuard.ts` |
| `check_assistant_history.py` | Q&A history stored per session: question and answer land in `biz_ai_session` / `biz_ai_message` (capability `knowledge-assistant`), replay carries mode and references, archived timestamps match so "add to knowledge base" never duplicates, foreign or other-module sessions are refused, over-limit replays only the newest and writes no `biz_ai_record` | `knowledge_assistant_service.history/record_qa` and `api/knowledge.py::_persist_qa` |
| `check_assistant_thinking.py` | Q&A assistant reasoning chain: `reasoningTokens` in the billing payload, `ask-stream` is SSE and ends with an `error` event when no AI profile is usable, reasoning is not persisted by default, opt-in save writes it and replay carries `thinkingText` / id | `knowledge_assistant_service.record_qa/history` and the frontend "visible in this session only" hint |
| `check_async_races.py` | Async scheduling races (B3): a matrix dispatched twice concurrently still runs once, cancelling a task no longer reports a false success (real state after the queued->running race) | matrix dispatch lock and the cancel branch in `services/job_queue.py` |
| `check_attachments.py` | Attachments: document import + image watermark removal + unified AI input (document-to-prompt, multimodal images, vision fallback) + attachments on requirement split, knowledge Q&A and AI revision + smart routing that converts input to text first + prompt-injection preprocessing (invisible chars / pseudo role markers / instruction-override markers) | attachment endpoints, Pillow, mock server output |
| `check_automation.py` | Automation script assets and execution engine: CRUD across four script categories, subprocess execution with traces, result write-back; `from-output` turns AI output into a script asset; AI one-click imports use separate numbers (`AI_AUTO-W/AI_AUTO-A/AI_AUTO-I/AI_PERF` and `AI_SQL` prefixes) without disturbing manual numbering | execution records and engine log (`data/aitp.log`) |
| `check_automation_writeback.py` | Automation result writeback to cases/rounds (GAP-07) | writeback service and round state machine |
| `check_backup_schedule.py` | Scheduled backups and retention (GAP-13) | backup service and `data/` retention logic |
| `check_bug_screenshots.py` | Defect numbering and screenshots (migration 074): source column with three prefixes (BUG / AI_BUG / Auto_BUG), globally unique defect numbers, no reuse after soft delete, numbering inside a write transaction, screenshot naming `pic_<no>_001` with 10MB per file / 30 per defect limits, sequence not rewound after deletion, screenshots excluded from bundle / asset package / retention unless enabled | `db/migrations/074_bug_numbering_screenshots.sql` and `repositories/bug_repo.py::next_bug_no` |
| `check_case_test_data.py` | Case steps/expectations paired line by line + test-data parsing in many formats (inline text/JSON, txt/csv/xlsx/json/sql files, URLs and unknown formats refused), automation consumes `AITP_TEST_DATA`, JMeter points at the parsed `testdata.csv` (BUG-44/45) | the test-data parsing service and case step/data persistence |
| `check_ci_boundary.py` | CI token access boundary: route allowlist, project scope, expiry, revocation, audit source (GAP-24) | CI token auth checks and audit source marking |
| `check_ci_notifications.py` | CI triggers and webhook notifications (GAP-10), self-hosted HTTP collector | token hashing, channel scoping, delivery records |
| `check_context_assembler.py` | Context assembly: three budget tiers, recall of modules/cases/defects/knowledge per project and feature point, preview and real build share one code path (GAP-27) | `backend/aitp/services/context_assembler.py` tiers and recall |
| `check_db.py` | Data layer: PRAGMA, FK constraints, seed volume, soft delete, concurrent writes | `backend/aitp/db` migrations and connection config |
| `check_db_checkpoint.py` | Database query checkpoints (Plan A): read-only gate (writes / multi-statements / INTO rejected), Redis read-only whitelist, Mongo query parsing, real SQLite execution of `db:` / `db:rows:` / `db:json:` assertions with `{{var}}` parameterization, row cap, scenario database steps with assertions / variable extraction, evidence keeps the query text, missing domestic-DB drivers get install hints | `services/db_checkpoint_service.py` and `interface_runner._eval_db_rule` |
| `check_db_concurrency.py` | Concurrent writes and id allocation: 16 parallel creates never collide on a unique key, reported success count equals rows landed, write-lock statistics (GAP-26) | `backend/aitp/db/write_guard.py` and each repository's id allocation |
| `check_dedup.py` | Generation dedup rules: normalization, threshold bands, mark vs increment behaviour, payload index alignment (GAP-29) | `backend/aitp/services/dedup_service.py` |
| `check_env_config.py` | Test environment config: CRUD, secret masking, health probe, unique default | env config endpoints and secret store |
| `check_error_boundary.py` | Error governance and frontend fallback: exceptions never swallowed, errors carry a traceId, long work leaves the request thread, empty states and retry (GAP-26) | the `except Exception` handling and `validation_jobs` |
| `check_execution_evidence.py` | Unified execution-evidence protocol: required-field matrix across the four execution paths, snapshots and missing-data degradation (GAP-32) | `execution_protocol.py` and `biz_execution_evidence` |
| `check_export_caps.py` | Export claims match implementation: Excel/Word allowlist from one source, Chinese degradation when unsupported (GAP-37) | the `capability_codes` allowlist and the help pages |
| `check_fts.py` | FTS5 hybrid search: Chinese short-word fallback, injection guard, index sync | search service and index rebuild logic |
| `check_functional_matrix.py` | Functional matrix: projects x boundaries x full paths (temp DB, self-contained) | locate the module endpoint by failed item name |
| `check_global_search.py` | Global search capability boundaries | global search and per-module indexes |
| `check_home_cards.py` | Home card customization: default layout, dirty-config cleanup, show/hide ordering | home config read/write and cleaning |
| `check_interface_runner.py` | Interface runner: request assembly, assertions, timeouts, same-origin limit, batch queue | execution engine and round writeback |
| `check_isolation_matrix.py` | Multi-project x multi-version data isolation audit (HTTP, long-running) | requires a running local service; rerun with `AITP_ACCESS_TOKEN` |
| `check_kb_ask.py` | Knowledge-base Q&A recall fallback (natural language must retrieve formal entries) | KB retrieval and `knowledge/` storage |
| `check_knowledge_governance.py` | P8 knowledge governance: approval, versioning, reference protection, reindex | governance services and Markdown persistence |
| `check_lab.py` | P6 AI lab | lab capability list and mock server output |
| `check_numbering_uniqueness.py` | Numbering backstop: unique indexes and column tuples for requirement / scenario / scenario-run numbers, same-number blocking with cross-project allowance, legacy upgrade de-duplication (keeps the lowest id, re-queues the rest behind the group maximum, never touches clean rows), number reuse after soft delete, every master numbering column and `next_no` call site registered, requirement external import key at the same strictness as cases and defects, matrix numbering holds the write lock | `db/migrations/075_no_unique_indexes.sql`, `repositories/numbering.py` and `services/execution_matrix_service.py` |
| `check_observability.py` | Observability: traceId across logs and response headers, only failures/slow requests logged (GAP-23) | `backend/aitp/observability` and log format |
| `check_onboarding.py` | First-run onboarding state (GAP-18) | migration 045 and the onboarding API |
| `check_output_schema.py` | Output structure validation: JSON Schema for four capabilities, blocking missing fields and type mismatches, retry semantics (GAP-28) | `backend/aitp/ai/output_schema.py` and the capability mapping |
| `check_p2.py` | P2 closure: schedule CRUD / trigger-now / due `_tick` dispatch, execution metrics, JMeter load model emitting `-Jthreads`, browser steps inside API scenarios (mock MCP); schedules carry SCH numbers, a script counts as "in use" only when referenced by a scenario automation step with refs returning the scenario SCN numbers (schedule-only references and execution history do not count), scenario disabled status persists while invalid status values are rejected | `api/schedules.py`, `services/test_schedule_service.py::_tick`, `services/automation_service.py::_jmeter_command` |
| `check_pagination.py` | List pagination and cursor continuation: no endpoint dumps whole tables, `total` matches row count, case tree pages by `afterId` (GAP-25) | `common/pagination.py` and the list endpoints |
| `check_portability.py` | Cross-platform delivery: preflight, PDF CJK fonts, secret-store fallback, script portability | printed platform-specific checks (never writes the real DB) |
| `check_prompt_regression.py` | Prompt-effect regression set: offline mock batch plus six-metric baseline (pass rate, retention rate, completeness, duplicate rate, issue count, prompt size) so edits are comparable (GAP-31) | `backend/prompts/regression/` samples and baseline file |
| `check_prompts.py` | Prompt library: parseable, variables complete, no render leftovers, capability alignment | `backend/prompts` YAML and capability mapping |
| `check_report_evidence.py` | Report failure samples and evidence drill-down: sample count matches real failures, all three export formats carry the chosen samples, Chinese degradation when no snapshot, and case-level-only snapshots resolve identically across report / drill-down / bug views (GAP-35) | the `failure-samples` routes in `api/reports.py` and `execution_protocol.latest_evidence_for_run_case` |
| `check_report_sources.py` | Reports sourced by round / execution matrix / scenario batch: source resolution, per-section metrics, statistics endpoint, failure-sample isolation, illegal sources refused | `services/report_source_service.py` and the report statistics endpoints |
| `check_report_trends.py` | Report trends and version diff (GAP-12) | report export and trend aggregation |
| `check_report_xss.py` | Markdown sanitising for the HTML / print report export: `attr_list`, `javascript:` links and raw HTML never reach the exported file, while normal tables, code blocks and safe links survive (render layer only, no DB, no model) | the sanitizer branch in `services/report_exporter.py` |
| `check_residual.py` | Standalone data pools (CRUD, duplicate-name guard, parse preview, `pool:<name>` consumed by scenario data driving); per-round environment-diff attribution (env-sensitive / consistent failure / no baseline / environment not bound); `automation.auto_bug` off by default, on it converts a failed run into one bug and never a second time | `repositories/data_pool_repo.py`, `services/auto_bug_service.py` and the round attribution logic |
| `check_routing.py` | Routing regression: 23 capability cards, each representative input must route correctly | routing rules and card config |
| `check_scope_enforcement.py` | Hard project-scope boundaries (supplementary gate): report fragments, report export and scenario runs never read across projects | the `projectId` ownership checks and fragment reference resolution |
| `check_script_asset.py` | Script version snapshots / diffs / review, the UI element library (Page Object, scripts look selectors up by name), and turning exploratory findings into replay scripts | `repositories/script_asset_repo.py` and the version / element-library routes in `api/automation.py` |
| `check_secret_residue.py` | GAP-22 secret-at-rest closure: reference storage, residue scan, migrate/rollback, dual-read | secret reference table and residue scan (temp DB only) |
| `check_stream_polling.py` | Streaming short-poll contract on both ends: launch returns only taskId plus the frames route, frame order and cursor, buffer-expiry fallback, and the frontend really consuming `mode:"poll"` and sending the current project (GAP-26 / GAP-29) | `api/lab.py` stream/frames and `frontend/src/api/stream.ts` |
| `check_security.py` | Security and compliance: secrets, injection, path safety, same-origin guard | `backend/aitp/common` security utilities and middleware |
| `check_task_labels.py` | Task-type labels: registered kinds and the label table agree in both directions, filter dropdown shares one source (GAP-37) | `KIND_LABELS` in `services/job_queue.py` |
| `check_test_data.py` | Test data generation (file branch) | generation service and exported files |
| `check_test_execution.py` | Execution loop: plan -> round -> batch results -> defect -> traceability -> release gate | locate the blueprint by failed step name |
| `check_test_round_redesign.py` | Test plan/round multi-round redesign: round types, plan time windows, scope presets, 4/3-round template generation (migration 057) | `test_round_service.py` and plan/round endpoints |
| `check_token_plan.py` | Token-plan billing: `billing_mode='token_plan'` deducts tokens against a purchased quota, shows the remainder and stores `cost_micros` as NULL (no money conversion); pay-as-you-go semantics unchanged (migration 060) | `sys_ai_profile.billing_mode/token_quota` and the write branches in `ai/cost.py` |
| `check_module_tree.py` | Feature-point tree: three levels, re-parenting, role by depth, capability binding and per-node prompt | `api/modules.py` and the module-tree repository |
| `check_execution_matrix.py` | Execution matrix: multi-env x multi-endpoint per-cell real runs, cross-cell attribution, common-failure-to-defect, production confirmation (GAP-43) | `execution_matrix_service.py` and `biz_execution_matrix*` |
| `check_exploratory_web.py` | Web exploratory testing: MCP stub driver, evidence-pack fields, dual-anchor ±5s log slices, findings to pending pool not defect store, no-model degradation, input masking (GAP-45) | `exploratory_service.py` and `biz_exploration_*` |
| `check_knowledge_recall.py` | Knowledge semantic-recall evaluation (D1 conditional): hybrid vs pure-vector/keyword recall hit comparison | the recall evaluation script and `docs/AITP-三期-D1-召回评估.md` |
| `check_knowledge_reuse.py` | Knowledge / report fragment cross-project reuse: fragment CRUD, reference relations, live body rendering, referenced-fragment delete protection (GAP-42) | `report_fragment_repo.py` and `biz_report_fragment*` |
| `check_retention.py` | Data retention and archival: per-type retention and expiry actions, reference protection, soft-delete + zip archive, original readback, startup auto-cleanup (GAP-44) | `retention_service.py` and `data/archive/` |
| `check_test_data_samples.py` | Test-data masked samples (GAP-39): missing sensitive-column config rejected, masked copy contains no raw PII, scheme reuse and expiry cleanup | the sample-pool service and masking engine |
| `check_project_package.py` | Project asset package (GAP-41): structure counts, no secrets, same-code rejection, rename-import and FK remapping | the asset-package service and manifest |
| `check_external_import.py` | External-platform read-only import (GAP-40): stub Zentao login-to-token, GET-only no writeback, mapping persistence, external-id dedup, two-layer auth, mapping templates and batch isolation, secrets not stored | `services/external_import*` and `api/external_imports.py` |
| `check_types.py` | Type gate: mypy --strict over the backend plus real `vue-tsc --noEmit` and `tsc -p tsconfig.e2e.json` (Playwright specs); `--require-frontend` fails CI without frontend deps | the file:line reported by mypy / TypeScript |
| `check_noise.py` | Code-noise gate: zero tolerance for TODO/FIXME markers, debugger/console.log/alert, backend print/breakpoint/pdb, commented-out code, unused Python imports (respecting `# noqa: F401`) and stray .tmp/.bak/.orig files | the file:line it reports |
| `check_walkthrough.py` | Fixed walkthrough: routing order + fallback order + behavioral-safety rules + structured records + global search | per-step assertion output |
| `check_fix_suggestion.py` | AI fix-suggestion loop: migration and numbering, schema validation, generate → adopt writeback → terminal state, dismiss, no-model fallback, project isolation (migration 076) | `services/fix_suggestion_service.py` and the fix-suggestion API |
| `check_kb_vector.py` | Knowledge vector search: chunking, incremental index, cosine and packing, fallback, RRF fusion, project isolation (migration 077) | `knowledge_embedding_service.py` and embedding config |
| `check_exec_media.py` | Execution media: persistence, size/suffix limits, safe retrieval, retention cleanup | `services/media_service.py` and exploratory runs |
| `check_auth_sessions.py` | Auth-session reuse: save/overwrite/expiry (cookie + JWT exp)/remove/cross-project isolation/path traversal/role sanitising (migration 079) | `services/auth_session_service.py` |
| `check_heuristics_pack.py` | Offline heuristics packs: four dictionaries load, keyword hits, capability filter and default category | `prompts/heuristics/` and `heuristics_service.py` |
| `check_requirement_import.py` | Requirement batch split: fingerprint, schema, split and batches, idempotent adoption, module resolution, cross-project isolation (migration 080) | `services/requirement_import_service.py` |
| `check_case_insight.py` | Execution trust and asset efficiency: attribution rules, flaky detection, case quality check, risk selection, import idempotency, local-diff patch adopt/dismiss/terminal state, pre-execution self-check, release readiness (migrations 081–083) | `services/failure_attribution_service.py`, `case_patch_service.py`, `case_quality_service.py`, `risk_selection_service.py`, `execution_precheck_service.py` |
| `check_v27.py` | V2.7 platform features: App scenario steps (via the `mock_adb.py` stub) on success and failure paths, four-channel template rendering with email required-field validation, notification preview, defect draft confirm/ignore, MCP switch/token generate-rotate-disable-enable-delete/one-time reveal/write access/call log, failure clustering, dependency dedup and cycle guard, impact closure (migrations 087–088) | `services/app_scenario_service.py`, `notification_service.py`, `bug_draft_service.py`, `mcp_service.py`, `insight_service.py` |
| `smoke_api.py` | P3 API smoke: all blueprints' key paths on a temp DB | the failure list printed at the end |

General triage: every script prints `[FAIL]` lines item by item and ends with `PASS=n FAIL=n`;
rerun a failing script alone with `python -X utf8 backend/scripts/<name>.py`, or rely on the
output tail the runner prints for failed scripts.

### CI and protected branches (manual remote setup)

The repo ships `.github/workflows/gates.yml` and `.workflow/gates.yml` (Gitee Go): install
dependencies, build the frontend, start the local service, run `run_all_checks.py`, and upload
the JSON report as an artifact. Neither workflow can be executed locally; treat the first remote
run as the source of truth. On CI `check_isolation_matrix` is excluded via `--skip-name`
(long-running audit, timing-sensitive), and `check_ai_live` is permanently excluded by the runner.

- GitHub: Settings -> Branches -> branch protection rule, enable "Require status checks to pass"
  and select the check named `gates` (must match the job name in the workflow).
- Gitee: enable Gitee Go for the repository (Settings -> Gitee Go), then require the pipeline
  check named `gates` for merge requests (matching the job name in `.workflow/gates.yml`;
  exact fields follow official Gitee Go docs).

---

## End-to-end tests (E2E)

Playwright-based full-flow E2E tests live in `frontend/e2e/`, configured by `frontend/playwright.config.ts`.
Currently **21 specs / 64 cases**, run serially; spec code shares the strict type check with the frontend (`tsconfig.e2e.json`).
The harness boots an **isolated environment** (separate backend on 8762 + temp DB, mock model on
18790/18791, vite on 5399) and never touches the live 8760 service or `data/aitp.db`; teardown kills the
process tree and removes the temp directory.

```bash
cd frontend && npx playwright install chromium   # first time
cd frontend && npx playwright test               # serial, workers=1
```

- **BFS**: visit every route once, asserting no blank screen / no JS exception / no 5xx.
- **DFS**: exhaust interactions on core pages (AI capability, cases, plan/round, defects, reports, knowledge, regression), capped at 20 elements per page to avoid loops.
- **Non-happy paths**: back/cancel/discard, empty data, very large data (1000 rows pagination + virtual window), unauthenticated/passcode, refresh/back, form boundaries (empty / over-long / special characters).
- **Q&A history survives** (`06-assistant-history`): after hopping through 8 menu pages — enough to evict the assistant from the `keep-alive` cache — the thread is still there, and a full reload still shows it (proving it comes from the database, not memory); after "clear conversation" the current thread is empty but the archived session remains queryable in "AI records -> Session records" filtered by "Knowledge-base Q&A".
- **Phase-four specs** (`13`–`17`): fix suggestions (fail → generate → adopt writeback / dismiss), vector search (configure embedding → rebuild → semantic recall + fallback), auth-session reuse (import → precheck → expiry block → remove), case mindmap (toggle → refresh restore → empty state), requirement batch split (split → tick and adopt → duplicate import skipped).
- **Execution-trust spec** (`18`): case quality check (scan → issue detail), pre-execution self-check + concurrent interface run with attribution, and the release-readiness verdict rendered on the traceability page.
- **Unified-attachments spec** (`19`): image-only requirement split, knowledge Q&A with an image, AI revision sending a reference screenshot, smart routing transcribing a screenshot before matching, and the AI-profile vision switch save/read-back.
- **V2.7 platform spec** (`21`): App scenario creation and execution (via the `mock_adb.py` stub, covering both pass and fail assertions), notification template preview and email channel save, defect draft confirm and ignore, MCP settings plus JSON-RPC calls (`tools/list` / `tools/call` / write-tool guard), one-time token reveal and old-token invalidation after rotation, and quality-insight failure clustering with change impact.
- Every step asserts a business result, not just "no error"; elements are re-located after page changes, never reusing stale refs.
- Results: `frontend/e2e/results.json` + console list; failures keep trace/screenshots.
- Convention in `AGENTS.md`: **after every code change, run the verification and report the real pass/fail results in the reply**.

> The production-confirmation checkbox dialog renders unstably under Playwright's bundled browser, so `05-prod-confirm` asserts the API-level guard; the dialog UI was verified manually in a headed browser.

---

## Directory layout

```
backend/
  aitp/
    api/          48 blueprints / 511 endpoints
    ai/           resolver, client, masking, adapters, cost, output_schema
    common/       constants, validation, pagination, keystore, security utilities
    db/           connections and migrations (84 files)
    repositories/ parameterized SQL repository layer
    services/     knowledge persistence, report export, backup/restore, execution matrix, retention/archival, exploratory testing, external import
    prompts/      prompt template library
    static/       built frontend assets
  prompts/        prompt YAML
  scripts/        gates and bootstrap scripts (incl. MCP / HTTP stub servers)
frontend/
  src/
    api/          unified request layer
    components/   shared components
    views/        64 page components
  e2e/            Playwright end-to-end tests (isolated harness)
data/             runtime data (SQLite, logs, keystore, archives, defect screenshots in bug-screenshots/, exploratory screenshots and log slices, execution media in exec-media/, auth sessions in auth-sessions/)
knowledge/        Markdown knowledge base storage
```

---

## Initialization and demo data

```bash
python backend/scripts/init_db.py        # initialize database and migrations
python backend/scripts/seed_demo.py      # write demo data into data/demo.db
```

Copy `.env.example` to `.env` and adjust as needed (`.env` is never committed).

---

## License

Licensed under the [Apache License 2.0](./LICENSE).
