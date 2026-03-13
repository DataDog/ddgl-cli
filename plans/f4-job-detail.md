# F4 — Job Detail Screen

## Context

The ddgl TUI currently shows a pipeline view with a job list. Users can browse jobs
but have no way to drill into a single job. This plan adds a full-screen job detail
view that serves two personas:

- **Project devs**: check PR status, read logs, debug failures, see test results
- **DevOps / CI maintainers**: analyze job performance, reliability, dependencies,
  and timing data from CI Visibility

The screen is designed as a stable shell (meta panel + tabbed content area) that
grows incrementally — each phase adds a tab without touching the others.

---

## Screen architecture

```
┌─ Header: Job #12345 — build-image ────────────────────────┐
├──────────────┬────────────────────────────────────────────┤
│  Job Meta    │  [Log] [Deps] [History] [Tests] [CI Vis]  │
│              ├────────────────────────────────────────────┤
│  build-image │                                            │
│              │                                            │
│  Stage       │  (active tab content)                      │
│  build       │                                            │
│              │                                            │
│  Status      │                                            │
│  ✗ failed    │                                            │
│              │                                            │
│  Duration    │                                            │
│  4m 12s      │                                            │
│              │                                            │
│  Runner      │                                            │
│  shared-01   │                                            │
│  [go, linux] │                                            │
│              │                                            │
│  Reason      │                                            │
│  script_fail │                                            │
│              │                                            │
│  URL         │                                            │
│  gitlab/...  │                                            │
├──────────────┴────────────────────────────────────────────┤
│  Footer: q Close  / Search  o Open URL                    │
└───────────────────────────────────────────────────────────┘
```

- **Full-screen `Screen[None]`** — pushed via `app.push_screen()`, popped with `Escape`/`q`
- **Left panel**: fixed-width (~28 cols) meta panel with enriched job data
- **Right panel**: `TabbedContent` — starts with Log only, tabs added per phase
- **`all_jobs: list[Job]`** passed from day 1 to enable future in-modal navigation

---

## Phase 1 — Screen skeleton + meta panel + streaming log ✅

Implemented. Files:
- `src/ddgl/tui/screens/__init__.py`, `src/ddgl/tui/screens/job_detail.py`
- `src/ddgl/model/job.py` — added `runner_description`, `runner_tags`, `queued_duration`, `needs`
- `src/ddgl/tui/widgets/job_list.py` — added `JobSelected` message
- `src/ddgl/tui/app.py` — `on_job_list_panel_job_selected` handler
- `src/ddgl/tui/app.tcss` — job detail screen styles
- `src/ddgl/tui/widgets/help.py` — removed "(coming soon)"
- `tests/tui/test_screens/test_job_detail.py` — 18 tests for `_render_meta`

---

## Phase 2 — Log search

### Goal
`/` or `Ctrl+F` opens a search input overlaid at the bottom of the log tab.
Matches are highlighted; `n`/`N` (or `Enter`/`Shift+Enter`) jump between them.

### Modified files
- **`src/ddgl/tui/screens/job_detail.py`**:
  - Add `Input` widget inside the Log tab (hidden by default)
  - On `/` or `Ctrl+F`: show the input, focus it
  - On input change: scan `RichLog` content, highlight matches
  - On `Escape`: hide search, return focus to log
  - On `n`/`N`: scroll to next/previous match

- **`src/ddgl/tui/app.tcss`** — style the search overlay

### Design note
`RichLog` doesn't natively support highlighting ranges. Approach: when a search
is active, re-render matching lines with highlighted spans. Cache original `Text`
objects so we can restore them when search clears.

---

## Phase 3 — Dependency graph

### Goal
A "Dependencies" tab showing the DAG neighborhood centered on the current job:
which jobs it depends on (upstream) and which jobs depend on it (downstream).

### Implementation
- **Data**: the `needs` field (added in Phase 1) gives direct upstream deps.
  To find downstream deps, scan all jobs' `needs` lists. When the Dependencies
  tab is selected, batch-fetch full details for all pipeline jobs using `get_jobs()`
  from `core/jobs.py` (concurrent + cached).

- **Widget**: use Textual's `Tree` widget:
  ```
  build-image
  ├── ⬆ Depends on
  │   ├── ✓ setup (success, 1m 2s)
  │   └── ✓ lint (success, 3m 15s)
  └── ⬇ Required by
      ├── ● deploy-staging (running, 2m 30s)
      └── ○ deploy-prod (pending)
  ```
  Each node shows status icon + job name + (status, duration).

### New files
- `src/ddgl/tui/widgets/job_dag.py` — `JobDAGWidget(Tree)` + `build_dag()` pure fn
- `tests/tui/test_widgets/test_job_dag.py`

### Modified files
- `src/ddgl/tui/screens/job_detail.py` — add Dependencies `TabPane`
- `src/ddgl/tui/app.tcss` — tree widget styles

---

## Phase 4 — Same-job history

### Goal
A "History" tab showing the last N runs of this job (by name) across recent
pipelines on the same ref. Answers "is this flaky?" and "is it getting slower?"

### Implementation
- Fetch recent pipelines: `list_pipelines(client, ref, count=10)` from `core/pipeline.py`
- For each pipeline, fetch jobs and find same-named job
- Display as `DataTable`: Pipeline | Status | Duration | Date

### New files
- `src/ddgl/tui/widgets/job_history.py` — `JobHistoryPanel` + `fetch_job_history()`
- `tests/tui/test_widgets/test_job_history.py`

### Performance note
Limit to 5-10 pipelines, leverage cache, load lazily on tab select.

---

## Phase 5 — Test results (placeholder)

### Goal
A "Tests" tab — details TBD, teammate developing "Unified Test Format".

### For now
- Empty `TabPane("Tests")` with placeholder message
- Data model in `src/ddgl/model/test_result.py`: `TestCase`, `TestSuite`

---

## Phase 6 — CI Visibility (Datadog integration)

### Goal
"CI Visibility" tab with metrics from Datadog: failure rate, duration percentiles,
timing breakdown (queue vs execution), trend data.

### Implementation outline
- **Config**: `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE` in `config.py`
- **Client**: new `src/ddgl/datadog/client.py` (httpx-based)
- **Display**: timing breakdown bar, failure rate, duration trend, flakiness score

### New files
- `src/ddgl/datadog/__init__.py`, `src/ddgl/datadog/client.py`
- `src/ddgl/tui/widgets/ci_visibility.py`

---

## Out of scope (separate plans)

- **Artifact browser + CLI download** — separate plan TBD
- **Log formatting / error extraction** — `format` module
- **Retry / Cancel actions** — not in this iteration
- **Collapsible log sections** — `format` module
- **n/p job navigation** — architecture ready (`all_jobs` passed), bindings deferred
