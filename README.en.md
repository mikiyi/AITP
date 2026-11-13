# AITestPlatform (AITP)

English | [简体中文](README.md)

A personal AI testing workstation — **single-user, local-first, offline-capable** — a quality-control tool built around **prompt engineering and data masking for LLM calls**.

It folds the AI lab, test cases, defects, log analysis, SQL analysis, regression, reports, knowledge base, task list and workspace configuration into one closed loop of LLM-assisted testing:
**requirement → test point → test case → execution → defect → regression → report → knowledge retention**, with todos moving the progress along at every step.

- All data lives in a local SQLite file (`data/aitp.db`), bound to this machine only — never exposed to the LAN or the internet.
- AI settings are read from local environment variables, from in-app configuration, and from local models (Ollama).
- When there is no cloud model, the model is unavailable, or the network is down, AITP degrades to local rules automatically and never blocks the main workflow.

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

Frontend: 54 routes / 57 page components. Backend: 40 blueprints / 404 endpoints, all under `/api/v1`.

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
- **Navigation groups**: the sidebar groups by purpose into 10 groups — AI Lab / Task Center / Test Plans / Test Assets / Quality Conclusions / Automation Scripts / Execution & Exploration / Knowledge Base / Data & Migration / Settings, plus Home and Help as direct entries.
- **Filters are queries**: changing a filter runs the query immediately — no "Search" button. Filters and pagination are kept in the URL, so links are shareable.

### Task list

Page: **Task list** (`/todos`)

- Top summary: open / due today / overdue / completed / completion rate
- Left views: all tasks, due today, due this week, overdue, pinned, completed
- Middle list: quick entry, filtering, bulk actions, export
- Right detail: status, priority, due date, category, tags, project, progress and notes, with subtasks
- Subtask completion automatically rolls up to the parent task

### AI lab

20 capabilities. Knowledge-base Q&A runs through the "Knowledge Base → Q&A assistant" chat panel:

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

- **Capability cards are driven by the prompt library**
- **Smart routing**: type one sentence and the best-matching capability is selected automatically
- **Pre-flight check**: before running, it lists missing items, completeness, primary/auxiliary tasks and execution order
- **Context assembly**: modules, existing cases, defects and knowledge are recalled per project/feature point and injected into the prompt; tiers are lite / standard / full, defaulting to standard (GAP-27)
- **Output schema validation**: results are validated against a JSON Schema; missing or mistyped fields are blocked on the spot with a retryable Chinese message (GAP-28)
- **Generation de-duplication**: new output is compared against cases already in the library, in "mark suspected" or "incremental against baseline" mode, and suspected items are unchecked by default (GAP-29)
- **Cost accounting**: per-profile unit prices convert every call into tokens and money; daily/monthly budgets and per-call token limits are soft warnings that never block (GAP-30)
- **Session records**: every run leaves a trace — input, output, model, usage and degradation
- **Streaming output**: the request returns a task id immediately and the frontend polls `framesUrl` for incremental frames (long connections no longer occupy sync workers); if the primary model fails, a degradation notice appears before switching models

### Test assets

| Module | Capabilities |
|---|---|
| Projects | CRUD, current-project switching, status management |
| Requirements | Maintenance, direct feature-point linking (module / sub-module / feature), bulk delete, export |
| Test cases | CRUD, status transitions, xlsx import/export, bulk execution |
| Defects | CRUD, state-machine transitions, linked test cases, log attachments, drill-down into original execution evidence |
| Assertion library | Reusable assertion rules shared per project/feature point, referenced from interface cases as `lib:<code>`, JSON Schema assertions with failure diffs |
| Knowledge base | Pending pool → official library, versioning, review, reference protection, full-text search |
| Test reports | Generation/adoption, ECharts charts, metric trends, export |
| Execution and evidence | Interface / automation / manual / regression / **exploratory** runs each write one unified evidence snapshot, drillable from runs, defects and reports |
| Regression checklist | CRUD, AI-generated regression items, auto items really executed by the server with evidence, manual items require evidence to pass |
| Execution matrix (GAP-43) | Fan a batch of cases across multiple environments × endpoints (app servers), dispatch once and really run each cell; per-case cross-cell attribution (common pass / common failure / env-specific / endpoint-specific / mixed); one-click defect conversion for common failures with evidence attached; production environments require explicit confirmation to dispatch |
| Web exploratory testing (GAP-45) | An MCP-driven browser explores in a read-snapshot → decide → act → observe loop, finding gaps that scripted cases miss; anomalous steps force 3 screenshots plus a dual-anchor ±N-second log slice, network snapshot, console errors and traceId as an evidence pack; password/token inputs are masked per PII rules; findings go to the pending pool for human review first and only become defects after approval |
| Knowledge / report fragment reuse (GAP-42) | A fragment is maintained once in its source project; target reports register a reference and rendering / export reads the body live, avoiding drifting copies; referenced fragments cannot be deleted |
| Data retention and archival (GAP-44) | Per-data-type retention days and expiry actions (archive / soft-delete / summary-only) for runs, automation runs, evidence, logs and todos; reference protection (bug-linked evidence, matrix-linked records survive expiry); expired data is zipped into `data/archive/` and searchable with original readback; dry-run preview and startup auto-cleanup supported |

### Workspace configuration

| Page | Contents |
|---|---|
| AI configuration | Provider profile CRUD, API keys in the OS keystore, priority ordering, real self-check, model list, profile unit prices and call cost, context tier and de-duplication mode, soft budget thresholds |
| Knowledge base settings | Storage directory, file mode, rebuild index on startup |
| Module tree | Three-level structure and capability binding |
| System settings | Read/write all configuration items |
| Test environment configuration | Multiple named environments, credentials in the system keystore, reachability probe |
| Data backup | Database snapshot backup, full-bundle backup, restore |
| Data retention and archival | Per-type retention days and expiry actions, expiry preview, dry-run, run cleanup now, archive search and original readback (GAP-44) |
| Exploratory testing settings | Max steps / duration / per-step timeout / screenshots, record selectors, input masking toggle, log-slice window and sources, MCP server command and pinned-tool allowlist (GAP-45) |
| CI and notifications | CI tokens, webhook channels, event subscriptions, test send |
| Home card customization | Show/hide and reorder non-fixed home cards |

---

## AI integration

Three-level priority: **workspace configuration (database) > environment variables > built-in defaults**

1. **Environment variables** (work out of the box)
   - Generic: `AITP_AI_PROVIDER` / `AITP_AI_BASE_URL` / `AITP_AI_API_KEY` / `AITP_AI_MODEL`
   - Per provider: `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` / `OLLAMA_HOST` …
2. **Workspace configuration**: add provider profiles on the AI configuration page and set their priorities
3. **Local models**: Ollama (`ollama_native` protocol)

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

Gate inventory (55 entries = 54 `check_*.py` scripts + `smoke_api.py`, one-to-one with the real
scripts under `backend/scripts`; `check_ai_live` is not run by the runner, so 54 items actually
execute):

| Script | Covers | When it fails, look at |
| --- | --- | --- |
| `check_ai_live.py` | Live SenseTime model full-path test (**never in the runner**: real calls, burns quota, non-deterministic) | run it manually; see env vars in the script header |
| `check_ai_masking.py` | AI outbound masking (phone / ID / email / bank card / API key) | `backend/aitp/ai/masking` rules and profile config |
| `check_ai_cost.py` | AI cost accounting: per-profile unit prices and micro-unit conversion, daily/monthly budgets and per-call token soft threshold, over-budget only warns and never blocks (GAP-30) | `backend/aitp/ai/cost.py` and the price fields of `sys_ai_profile` |
| `check_attachments.py` | Attachments: document import + image watermark removal | attachment endpoints, Pillow, mock server output |
| `check_automation.py` | Automation script assets and execution engine | execution records and engine log (`data/aitp.log`) |
| `check_automation_writeback.py` | Automation result writeback to cases/rounds (GAP-07) | writeback service and round state machine |
| `check_backup_schedule.py` | Scheduled backups and retention (GAP-13) | backup service and `data/` retention logic |
| `check_ci_boundary.py` | CI token access boundary: route allowlist, project scope, expiry, revocation, audit source (GAP-24) | CI token auth checks and audit source marking |
| `check_ci_notifications.py` | CI triggers and webhook notifications (GAP-10), self-hosted HTTP collector | token hashing, channel scoping, delivery records |
| `check_context_assembler.py` | Context assembly: three budget tiers, recall of modules/cases/defects/knowledge per project and feature point, preview and real build share one code path (GAP-27) | `backend/aitp/services/context_assembler.py` tiers and recall |
| `check_db.py` | Data layer: PRAGMA, FK constraints, seed volume, soft delete, concurrent writes | `backend/aitp/db` migrations and connection config |
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
| `check_observability.py` | Observability: traceId across logs and response headers, only failures/slow requests logged (GAP-23) | `backend/aitp/observability` and log format |
| `check_onboarding.py` | First-run onboarding state (GAP-18) | migration 045 and the onboarding API |
| `check_output_schema.py` | Output structure validation: JSON Schema for four capabilities, blocking missing fields and type mismatches, retry semantics (GAP-28) | `backend/aitp/ai/output_schema.py` and the capability mapping |
| `check_pagination.py` | List pagination and cursor continuation: no endpoint dumps whole tables, `total` matches row count, case tree pages by `afterId` (GAP-25) | `common/pagination.py` and the list endpoints |
| `check_portability.py` | Cross-platform delivery: preflight, PDF CJK fonts, secret-store fallback, script portability | printed platform-specific checks (never writes the real DB) |
| `check_prompt_regression.py` | Prompt-effect regression set: offline mock batch plus six-metric baseline (pass rate, retention rate, completeness, duplicate rate, issue count, prompt size) so edits are comparable (GAP-31) | `backend/prompts/regression/` samples and baseline file |
| `check_prompts.py` | Prompt library: parseable, variables complete, no render leftovers, capability alignment | `backend/prompts` YAML and capability mapping |
| `check_report_evidence.py` | Report failure samples and evidence drill-down: sample count matches real failures, all three export formats carry the chosen samples, Chinese degradation when no snapshot, and case-level-only snapshots resolve identically across report / drill-down / bug views (GAP-35) | the `failure-samples` routes in `api/reports.py` and `execution_protocol.latest_evidence_for_run_case` |
| `check_report_trends.py` | Report trends and version diff (GAP-12) | report export and trend aggregation |
| `check_routing.py` | Routing regression: 20 capability cards, each representative input must route correctly | routing rules and card config |
| `check_secret_residue.py` | GAP-22 secret-at-rest closure: reference storage, residue scan, migrate/rollback, dual-read | secret reference table and residue scan (temp DB only) |
| `check_stream_polling.py` | Streaming short-poll contract on both ends: launch returns only taskId plus the frames route, frame order and cursor, buffer-expiry fallback, and the frontend really consuming `mode:"poll"` and sending the current project (GAP-26 / GAP-29) | `api/lab.py` stream/frames and `frontend/src/api/stream.ts` |
| `check_security.py` | Security and compliance: secrets, injection, path safety, same-origin guard | `backend/aitp/common` security utilities and middleware |
| `check_task_labels.py` | Task-type labels: registered kinds and the label table agree in both directions, filter dropdown shares one source (GAP-37) | `KIND_LABELS` in `services/job_queue.py` |
| `check_test_data.py` | Test data generation (file branch) | generation service and exported files |
| `check_test_execution.py` | Execution loop: plan -> round -> batch results -> defect -> traceability -> release gate | locate the blueprint by failed step name |
| `check_test_round_redesign.py` | Test plan/round multi-round redesign: round types, plan time windows, scope presets, 4/3-round template generation (migration 057) | `test_round_service.py` and plan/round endpoints |
| `check_module_tree.py` | Feature-point tree: three levels, re-parenting, role by depth, capability binding and per-node prompt | `api/modules.py` and the module-tree repository |
| `check_execution_matrix.py` | Execution matrix: multi-env x multi-endpoint per-cell real runs, cross-cell attribution, common-failure-to-defect, production confirmation (GAP-43) | `execution_matrix_service.py` and `biz_execution_matrix*` |
| `check_exploratory_web.py` | Web exploratory testing: MCP stub driver, evidence-pack fields, dual-anchor ±5s log slices, findings to pending pool not defect store, no-model degradation, input masking (GAP-45) | `exploratory_service.py` and `biz_exploration_*` |
| `check_knowledge_recall.py` | Knowledge semantic-recall evaluation (D1 conditional): hybrid vs pure-vector/keyword recall hit comparison | the recall evaluation script and `docs/AITP-三期-D1-召回评估.md` |
| `check_knowledge_reuse.py` | Knowledge / report fragment cross-project reuse: fragment CRUD, reference relations, live body rendering, referenced-fragment delete protection (GAP-42) | `report_fragment_repo.py` and `biz_report_fragment*` |
| `check_retention.py` | Data retention and archival: per-type retention and expiry actions, reference protection, soft-delete + zip archive, original readback, startup auto-cleanup (GAP-44) | `retention_service.py` and `data/archive/` |
| `check_test_data_samples.py` | Test-data masked samples (GAP-39): missing sensitive-column config rejected, masked copy contains no raw PII, scheme reuse and expiry cleanup | the sample-pool service and masking engine |
| `check_project_package.py` | Project asset package (GAP-41): structure counts, no secrets, same-code rejection, rename-import and FK remapping | the asset-package service and manifest |
| `check_external_import.py` | External-platform read-only import (GAP-40): stub Zentao login-to-token, GET-only no writeback, mapping persistence, external-id dedup, two-layer auth, mapping templates and batch isolation, secrets not stored | `services/external_import*` and `api/external_imports.py` |
| `check_types.py` | Type gate: mypy --strict over the backend plus real `vue-tsc --noEmit`; `--require-frontend` fails CI without frontend deps | the file:line reported by mypy / TypeScript |
| `check_walkthrough.py` | Fixed walkthrough: routing order + fallback order + behavioral-safety rules + structured records + global search | per-step assertion output |
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
The harness boots an **isolated environment** (separate backend on 8762 + temp DB, mock model on
18790/18791, vite on 5199) and never touches the live 8760 service or `data/aitp.db`; teardown kills the
process tree and removes the temp directory.

```bash
cd frontend && npx playwright install chromium   # first time
cd frontend && npx playwright test               # serial, workers=1
```

- **BFS**: visit every route once, asserting no blank screen / no JS exception / no 5xx.
- **DFS**: exhaust interactions on core pages (AI capability, cases, plan/round, defects, reports, knowledge, regression), capped at 20 elements per page to avoid loops.
- **Non-happy paths**: back/cancel/discard, empty data, very large data (1000 rows pagination + virtual window), unauthenticated/passcode, refresh/back, form boundaries (empty / over-long / special characters).
- Every step asserts a business result, not just "no error"; elements are re-located after page changes, never reusing stale refs.
- Results: `frontend/e2e/results.json` + console list; failures keep trace/screenshots.
- Convention in `AGENTS.md`: **after every code change, run the verification and report the real pass/fail results in the reply**.

> The production-confirmation checkbox dialog renders unstably under Playwright's bundled browser, so `05-prod-confirm` asserts the API-level guard; the dialog UI was verified manually in a headed browser.

---

## Directory layout

```
backend/
  aitp/
    api/          40 blueprints / 404 endpoints
    ai/           resolver, client, masking, adapters, cost, output_schema
    common/       constants, validation, pagination, keystore, security utilities
    db/           connections and migrations (58 files)
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
    views/        57 page components
  e2e/            Playwright end-to-end tests (isolated harness)
data/             runtime data (SQLite, logs, keystore, archives, exploratory screenshots and log slices)
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
