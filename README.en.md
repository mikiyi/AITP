# AITestPlatform (AITP)

English | [简体中文](README.md)

A personal AI testing workstation — **single-user, local-first, offline-capable** — a quality-control tool built around **prompt engineering and data masking for LLM calls**.

It folds the AI lab, test cases, defects, log analysis, SQL analysis, regression, reports, knowledge base, task list and workspace configuration into one closed loop of LLM-assisted testing:
**requirement → test point → test case → execution → defect → regression → report → knowledge retention**, with todos moving the progress along at every step.

- All data lives in a local SQLite file (`data/aitp.db`), bound to this machine only — never exposed to the LAN or the internet.
- AI settings are read from local environment variables, from in-app configuration, and from local models (Ollama).
- When there is no cloud model, the model is unavailable, or the network is down, AITP degrades to local rules automatically and never blocks the main workflow.

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

Frontend: 45 routes / 46 page components. Backend: 33 blueprints / 310 endpoints, all under `/api/v1`.

### Home

- Overview metrics: projects, test cases, defects, knowledge awaiting review
- Quick access: top 10 entries by real click count (including AI capabilities & module tree, assets, system settings); never-clicked entries fill remaining slots in original order
- Recent reports and defect activity
- Customizable cards: see **Settings → Home card customization** (`/settings/home-cards`)

### Appearance and navigation

- **10 themes**: Light, Dark, Blue, Red, Pink, Orange, Tech, Ocean, Botanical, Industrial. One click on the palette in the top-right applies the theme site-wide.
- **Navigation groups**: the sidebar groups by purpose — AI Lab / Test Plans / Test Assets / **Quality Analysis** / Automation / Knowledge Base / Settings.
- **Filters are queries**: changing a filter runs the query immediately — no "Search" button. Filters and pagination are kept in the URL, so links are shareable.

### Task list

Page: **Task list** (`/todos`)

- Top summary: open / due today / overdue / completed / completion rate
- Left views: all tasks, due today, due this week, overdue, pinned, completed
- Middle list: quick entry, filtering, bulk actions, export
- Right detail: status, priority, due date, category, tags, project, progress and notes, with subtasks
- Subtask completion automatically rolls up to the parent task

### AI lab

19 capabilities. Knowledge-base Q&A runs through the "Knowledge Base → Q&A assistant" chat panel:

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
| Prompt optimization | `prompt_optimize` |

- **Capability cards are driven by the prompt library**
- **Smart routing**: type one sentence and the best-matching capability is selected automatically
- **Pre-flight check**: before running, it lists missing items, completeness, primary/auxiliary tasks and execution order
- **Session records**: every run leaves a trace — input, output, model, usage and degradation
- **Streaming output**: SSE increments; if the primary model fails, a degradation notice appears before switching models

### Test assets

| Module | Capabilities |
|---|---|
| Projects | CRUD, current-project switching, status management |
| Requirements | Maintenance, module linking, bulk delete, export |
| Test cases | CRUD, status transitions, xlsx import/export, bulk execution |
| Defects | CRUD, state-machine transitions, linked test cases, log attachments |
| Knowledge base | Pending pool → official library, versioning, review, reference protection, full-text search |
| Test reports | Generation/adoption, ECharts charts, metric trends, export |
| Regression checklist | CRUD, AI-generated regression items, per-item execution results written back |

### Workspace configuration

| Page | Contents |
|---|---|
| AI configuration | Provider profile CRUD, API keys in the OS keystore, priority ordering, real self-check, model list |
| Knowledge base settings | Storage directory, file mode, rebuild index on startup |
| Module tree | Three-level structure and capability binding |
| System settings | Read/write all configuration items |
| Test environment configuration | Multiple named environments, credentials in the system keystore, reachability probe |
| Data backup | Database snapshot backup, full-bundle backup, restore |
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

---

## Data and knowledge governance

- Official knowledge entries are persisted as **Markdown** (`knowledge/`); SQLite stores only the index and metadata
- **Pending pool**: AI output lands there first and only becomes official after human review
- **Q&A assistant, three modes**:
  - `llm`: answers from the model's own knowledge (sends data out)
  - `kb`: local retrieval only, returns verbatim excerpts (no egress)
  - `hybrid` (default): retrieves the knowledge base first and feeds matched text tagged `[K1]…` to the model for consolidation (sends data out)

---

## Security boundaries

- **Local only**: binds `127.0.0.1` by default
- **Same-origin protection**: write endpoints must come from a local page
- **Local access passcode (optional)**: enable it under Settings → Access passcode; it expires after 12 hours
- **Secret storage**: API keys live in the OS keystore (Windows DPAPI / macOS Keychain / Linux Fernet)
- **Masking on AI egress**: cloud calls automatically replace phone numbers, ID numbers, emails, bank card numbers, API keys and more
- **AI behavior safety**: states insufficient evidence instead of inventing it; never fabricates knowledge titles or defect IDs; AI never approves a release; P0/P1 and prompt test results always require human review

---

## Quality gates

```bash
python -m mypy                          # backend type gate
python backend/scripts/smoke_api.py     # API smoke test
python backend/scripts/check_security.py     # secrets / injection / path security regression
python backend/scripts/check_db.py           # migration and data-layer regression
python backend/scripts/check_fts.py          # full-text search regression
cd frontend && npm run build   # vue-tsc --noEmit && vite build
```

---

## Directory layout

```
backend/
  aitp/
    api/          33 blueprints / 310 endpoints
    ai/           resolver, client, masking, adapters
    common/       constants, validation, pagination, keystore, security utilities
    db/           connections and migrations
    repositories/ parameterized SQL repository layer
    services/     knowledge persistence, report export, backup/restore, AI self-check
    prompts/      prompt template library
    static/       built frontend assets
  prompts/        prompt YAML
  scripts/        gates and bootstrap scripts
frontend/
  src/
    api/          unified request layer
    components/   shared components
    views/        46 page components
data/             runtime data (SQLite, logs, keystore)
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
