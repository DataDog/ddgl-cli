# Developer Guide

Reference for contributors: architecture, design principles, and layer-by-layer breakdown.

## Table of Contents

1. [Setup](#setup)
2. [Project Structure](#project-structure)
3. [Architecture Overview](#architecture-overview)
4. [Design Principles](#design-principles)
5. [Layer Reference](#layer-reference)
   - [Model](#model)
   - [Client](#client)
   - [Core](#core)
   - [Cache](#cache)
   - [CLI](#cli)
   - [TUI](#tui)
   - [Format & Render](#format--render)
6. [Testing](#testing)

---

## Setup

```bash
uv sync
uv run pytest -v
uv run ruff check --fix
```

---

## Project Structure

```
src/ddgl/
├── client.py               low-level async GitLab API wrapper
├── constants.py            enums (PipelineStatus, JobStatus), TTLs
├── exceptions.py           exception hierarchy
├── git.py                  async git helpers (detect project, current branch)
├── shell.py                sync + async subprocess wrappers
│
├── config/                 Config resolution — env vars, config file, git detection
│   └── loader.py           Config struct + load_config()
├── model/                  domain types (mostly msgspec.Struct)
│   └── config.py           ConfigFile — TOML config-file schema
├── core/                   business logic — no I/O, no UI
├── cache/                  multi-backend cache layer
│   └── backends/
├── cli/                    Click commands
├── format/                 trace parsing and rendering
├── render/                 Rich output for CLI commands
└── tui/                    Textual TUI application
    ├── screens/
    └── widgets/
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│  CLI (cli/)          TUI (tui/)                      │
│  Click commands      Textual app                     │
│       │                   │                          │
│       └──────────┬────────┘                          │
│                  ▼                                   │
│           core/  (business logic)                    │
│           pipeline.py  jobs.py  logs.py              │
│                  │                                   │
│         ┌────────┴────────┐                          │
│         ▼                 ▼                          │
│    client.py          cache/                         │
│    GitLabClient       Cache + backends               │
│    (httpx)            (SQLite, JSON, files)          │
└─────────────────────────────────────────────────────┘
```

Both CLI and TUI are presentation layers. Neither contains business logic. They call `core/` functions, which in turn call the client and cache. The TUI does **not** import from `cli/`.

---

## Design Principles

### Plumbing vs. presentation

`core/` contains the domain logic: resolving pipelines, filtering jobs, fetching logs, deciding what to cache and when. It has no knowledge of Click, Rich, or Textual. This makes it independently testable and reusable from any context (CLI, TUI, scripts, tests).

`cli/` and `tui/` are thin wrappers. Their job is to wire user input into `core/` calls and render the results.

### Cache-awareness lives in core, not in the client

The client (`client.py`) only caches raw API responses keyed by URL+params hash — a low-level HTTP cache. Structured object caching (pipelines, jobs, logs) is the responsibility of `core/`. Core decides *what* to cache (only finished/terminal objects) and *for how long*.

### Only terminal objects are cached durably

A pipeline or job in a running state can change. Only objects in a terminal state (`SUCCESS`, `FAILED`, `CANCELED`, `SKIPPED`) are written to the long-lived object cache with a 1-week TTL. Running objects always get a short TTL.

### Async throughout

All I/O is async (httpx, subprocess via `asyncio.create_subprocess_exec`). Bulk operations use `asyncio.gather()` for concurrency. The CLI bridges into async via `asyncio.run()` at the top of each command.

### msgspec.Struct for domain types

Domain types (`Pipeline`, `Job`, `LogLine`, `Section`) are `msgspec.Struct` — immutable, fast to serialize, and strict on deserialization. This is what makes the struct SQLite backend efficient: it serializes struct fields directly into columns.

### Hermetic tests

Tests do not share state with production code. Fakes and stubs for types used only as scaffolding (e.g. `FakeCache`) are defined locally in the test tree, not imported from production modules. A test should not break because an unrelated production definition changed.

---

## Layer Reference

### Model

`src/ddgl/model/` — domain types, no I/O.

#### `Pipeline` and `Job` (msgspec.Struct)

Both are constructed via a `from_api(data: dict)` classmethod that maps GitLab API JSON to typed fields. Status fields are typed as `PipelineStatus` / `JobStatus` StrEnums (defined in `constants.py`).

Key computed properties on `Pipeline`:
- `is_running` — status in `PIPELINE_RUNNING`
- `is_finished` — status in `PIPELINE_FINISHED`
- `elapsed` — `timedelta` from `created_at` to `finished_at` (or now if still running)

Key computed properties on `Job`:
- `has_failed` — `status == JobStatus.FAILED`, regardless of `allow_failure`
- `is_blocking` — `has_failed and not allow_failure`; failed in a way that actually fails the pipeline. A job with `allow_failure: true` that failed has `has_failed=True` but `is_blocking=False` — use this whenever "failed" is meant to exclude allowed failures (failure rollups, `-f/--failed`, etc.)
- `is_running` — status in `JOB_RUNNING`

#### `Page[T]`

Wraps a single page of paginated results plus GitLab's pagination headers (`x-page`, `x-total-pages`, `x-next-page`, `x-total`). Used as the return type of `fetch_*` methods on the client.

```python
page = await client.fetch_pipelines(ref="main", per_page=10)
if page.has_next:
    print(f"page {page.page} of {page.total_pages}")
```

#### Trace IR (`model/trace.py`)

The log parser produces a tree of `Section` and `LogLine` nodes:

```
Trace
├── Section("build", collapsed=False)
│   ├── LogLine("$ cargo build", ...)
│   └── LogLine("   Compiling foo v1.0", ...)
├── LogLine("some output outside sections", ...)
└── Section("test", collapsed=True)
    └── ...
```

`LogLine` carries the original `raw` text plus a cleaned `text` (markers stripped, ANSI preserved), an optional ISO timestamp, and the stream type (`STDOUT`/`STDERR`).

---

### Client

`src/ddgl/client.py` — thin async wrapper around the GitLab REST API.

`GitLabClient` is an async context manager backed by `httpx.AsyncClient`. It handles authentication (via `PRIVATE-TOKEN` header), error translation, and optional low-level API response caching.

#### Method signatures

Every public method follows the same shape: **the primary subject of the call is positional, everything else is keyword-only.** The subject is the resource id (`get_job(job_id)`, `get_all_jobs(pipeline_id)`) or, for the pipeline-list family, the ref being listed (`get_all_pipelines(ref)`). Options are never positional — they read as anonymous values at the call site, and adding a parameter mid-list silently changes what existing positional arguments bind to.

Options then split three ways:

| Kind | Params | Why |
| --- | --- | --- |
| ddgl concerns | `project_id`, `fresh` | Not GitLab query params at all — `project_id` is a path component, `fresh` maps to a cache `ttl` and is never sent upstream. Always explicit. |
| Common GitLab query params | `ref`, `per_page`, `scope` | Used across many call sites; explicit and typed for discoverability. |
| Everything else | `**params` | Forwarded verbatim as query parameters. A one-off param (`include_retried`, and later `order_by`, `source`, `updated_after`, …) needs no signature change on every method in the family. |

Single-object getters (`get_pipeline`, `get_job`, `retry_job`, `retry_pipeline`) take no `**params` — those endpoints accept no meaningful query parameters.

> [!WARNING]
> Because `**params` is forwarded blind, a misspelled name is **silently ignored** rather than raising `TypeError`. Two rules keep that safe:
>
> 1. **Don't hand-write a raw param at a call site.** Wrap it in a named, documented method — `get_job_attempts(pipeline_id)` is the reference example, being `get_all_jobs(..., include_retried=True)` with a docstring explaining what "attempts" means and why it's always `fresh`.
> 2. **Promote on second use.** Any param that acquires a second caller graduates to an explicit keyword argument.

#### Pagination methods

Every paginated resource exposes three variants following a consistent naming convention:

| Prefix     | Returns                  | Use when                                            |
| ---------- | ------------------------ | --------------------------------------------------- |
| `get_`     | `T`                      | fetching a single object by ID                      |
| `fetch_`   | `Page[T]`                | you only need the first page or want manual control |
| `iter_`    | `AsyncIterator[Page[T]]` | streaming large result sets                         |
| `get_all_` | `list[T]`                | result set is bounded (e.g. jobs for one pipeline)  |

All `iter_` and `get_all_` methods raise `PaginationLimitError` after `MAX_PAGES` pages (default: 50). If you hit this in practice, something has gone wrong.

`get_all_` methods (`_get_all` internally) fetch page 1 first, then fetch the remaining pages **concurrently** — bounded by `MAX_CONCURRENT_PAGE_FETCHES` — rather than one at a time, since page 1's `x-total-pages` header tells us up front how many pages there are. This collapses N sequential round-trips into ~2 for a large result set (e.g. a pipeline with hundreds of jobs). `iter_` methods stay strictly sequential — they're a streaming API, so there's no "total" to fetch ahead of. `get_all_`'s `PaginationLimitError` check also differs slightly as a result: it fails fast against page 1's header, before firing the concurrent batch, rather than mid-loop like `iter_`.

#### Error handling

`_raise_for_status()` translates HTTP errors:
- `404` on the project itself → `ConfigError` (project not found / wrong ID)
- `404` on a sub-resource → `NotFoundError` (resource doesn't exist)
- Other HTTP errors → `GitLabAPIError` (has `is_retryable` property for `RETRYABLE_STATUS_CODES`, `{408, 429, 500, 502, 503, 504}` — a shared constant in `exceptions.py`, not duplicated in `client.py`)

#### Retry

`_get_response()` (the raw HTTP GET underneath `_get`/`_get_text`/`_get_page`, so every `get_`/`fetch_`/`iter_`/`get_all_` method benefits uniformly) retries a connection-level failure or a `RETRYABLE_STATUS_CODES` response up to `HTTP_RETRY_ATTEMPTS` (3) times, backing off `HTTP_RETRY_BACKOFF_INITIAL_SECONDS * HTTP_RETRY_BACKOFF_MULTIPLIER ** attempt` (`0.5s`, then `1.0s`) between attempts. A `429`'s `Retry-After` header, when present, is honored in place of the computed backoff. Retry happens *before* `_raise_for_status()` runs — it only ever sees the final response, so error *mapping* (`ConfigError`/`NotFoundError`/`GitLabAPIError`) is unaffected by retrying.

These `HTTP_RETRY_*` constants are **transport** retry — resending an HTTP request that failed in flight. Unrelated to retrying a *CI job*, which is the `ddgl retry` feature.

#### Low-level API caching

`_get()` accepts a `ttl` parameter. If `ttl > 0` and a cache is open, the raw JSON response is stored in `CacheNS.API_RESPONSES` keyed by a hash of the path and query params. This is separate from the structured object cache managed by `core/`.

`get_pipeline()` and `get_all_jobs()` additionally accept `fresh: bool = False`, which passes `ttl=0` instead of the default TTL for that one call — bypassing this low-level cache entirely (read and write). Used by `core/attach.py`'s poll loop: polling a running pipeline through the normal 30s/15s cached reads would make the poll interval meaningless.

---

### Core

`src/ddgl/core/` — business logic, no UI, no rendering.

Core functions take `client` and an optional `cache` and return domain objects. They are the canonical way to fetch data from any context (CLI, TUI, scripts).

#### `core/pipeline.py`

```python
async def resolve_pipeline(
    client: GitLabClient,
    ref: str | None = None,
    pipeline_id: int | None = None,
    depth: int = 10,
    cache: Cache | None = None,
) -> Pipeline
```

This is the main entry point used by CLI commands. If `pipeline_id` is given, it calls `get_pipeline()` directly. Otherwise it resolves the ref (defaulting to the current git branch) and calls `find_latest_pipeline()`, which walks `depth` commits looking for a pipeline.

```python
async def get_pipelines(
    client: GitLabClient,
    pipeline_ids: Iterable[int],
    cache: Cache | None = None,
) -> list[Pipeline]
```

Bulk-fetch with cache optimization: one SQLite query for all cached IDs, then `asyncio.gather()` for misses. Returns results in input order.

#### `core/jobs.py`

```python
async def get_jobs(
    client: GitLabClient,
    job_ids: Iterable[int],
    cache: Cache | None = None,
) -> list[Job]
```

Same pattern as `get_pipelines`: one bulk cache read, parallel fetch for misses. Only jobs in terminal states are written to cache.

```python
async def list_jobs(
    client: GitLabClient,
    pipeline_id: int,
    scope: list[JobStatus] | None = None,
    cache: Cache | None = None,
) -> AsyncIterator[Job]
```

Streams jobs via the API. Caches each terminal job as it passes through.

```python
def filter_jobs(
    jobs: Iterable[Job] | AsyncIterable[Job],
    failed_only: bool = False,
    include_allowed_failures: bool = False,
    name_pattern: str | None = None,
    stage: str | None = None,
) -> list[Job] | AsyncIterator[Job]
```

Overloaded: returns a `list` for sync input, `AsyncIterator` for async input. Filters compose with AND. `name_pattern` is a compiled regex. `failed_only` gates on `Job.is_blocking` (excludes allowed failures); `include_allowed_failures` restores the pre-`is_blocking` behaviour of also matching jobs with `allow_failure` set (CLI: `--include-allowed-failures`).

#### `core/logs.py`

```python
async def get_logs(
    client: GitLabClient,
    job_ids: Iterable[int],
    cache: Cache | None = None,
) -> dict[int, str]
```

Returns raw log text keyed by job ID. Caches only when the log tail contains a GitLab completion marker (`"Job succeeded"`, `"Job failed"`, `"ERROR: Job failed"`), ensuring we never cache incomplete logs from running jobs.

```python
async def stream_log(
    client: GitLabClient,
    job_id: int,
    cache: Cache | None = None,
) -> AsyncIterator[str]
```

Cache-first: if the log is cached, yields lines from cache. Otherwise streams from the API and caches if the log turns out to be complete.

#### `core/attach.py`

```python
async def attach(
    client: GitLabClient,
    *,
    ref: str | None = None,
    pipeline_id: int | None = None,
    depth: int = 10,
    interval: float = 10.0,
    heartbeat: bool = False,
    wait_for_start: bool = True,
    follow: bool = False,
    timeout: float | None = None,
    cache: Cache | None = None,
    estimator: DurationEstimator | None = None,
) -> AsyncIterator[AttachEvent]
```

Powers `ddgl attach`: blocks on a CI pipeline, yielding `AttachEvent`s until it reaches a terminal state or `timeout` elapses. Stateless — no retry/resume concept. Every call resolves (or waits for) the pipeline, emits an early snapshot, fetches jobs and emits a full snapshot, then polls every `interval` seconds. A changed tick yields its pipeline/job transitions followed by one `poll` rollup; a quiet tick yields only the opt-in `heartbeat` rollup. Re-invoking after a stop (e.g. a calling harness enforced its own timeout) just runs this same sequence again against GitLab, which is the whole resumability story.

The **early snapshot** fires immediately after resolving the pipeline — before fetching jobs, which GitLab has no count endpoint for and can take real time on a pipeline with hundreds of jobs even with parallel pagination (below). Without it, `attach` would sit silent for that entire fetch. Its `jobs_total`/`jobs_done`/`current_stage` are `None`/unset (see `AttachEvent`'s docstring); renderers must treat `jobs_total is None` as "still loading," not zero jobs.

Polling reads bypass the client's response cache (`fresh=True` on `get_pipeline`/`get_all_jobs` — see [Client](#client)) so `interval` is the true detection latency, not `max(interval, cache_ttl)`. `get_all_jobs` also fetches its pages concurrently (see [Client](#client) → Pagination methods), which matters here: on a large pipeline the sequential-pagination job fetch used to take over a minute. Terminal jobs and `SUCCESS` pipelines are still written to the durable object cache from the poll loop, mirroring `core/jobs.py` and `core/pipeline.py`'s own rules, since polling bypasses their cache-write wrappers.

`AttachEvent` (`model/attach.py`) is one `kind`-tagged `msgspec.Struct` (`snapshot` / `job` / `pipeline` / `poll` / `heartbeat` / `switched` / `result`) rather than a class hierarchy — renderers switch on `.kind`. Rollup fields (`pipeline_id`, `ref`, `current_stage`, `pipeline_elapsed`, `jobs_total`, `jobs_done`, `failed_jobs`, `eta_seconds`) are populated on every event that has a resolved pipeline via an internal `_context()` helper, not just snapshot/poll/heartbeat — a renderer should never need to track cross-event state, or hold a `Pipeline`/`Job` object, to answer "how many jobs are done right now." The early snapshot above is the one exception (no jobs loaded yet to compute a rollup from). If you add a new event construction site, get its fields from `**ctx` rather than setting them by hand — a past bug had `job`/`heartbeat` events silently falling through to `pipeline_id=None` because they were built without it.

`current_stage` is the OLDEST stage that still has an incomplete job (the one actually holding up progress), not the most-recently-started one — GitLab's jobs endpoint returns jobs newest-ID-first with no stage-sequence field, so "first in the list" is not a reliable proxy for "most advanced."

A `GitLabAPIError` while *resolving* the pipeline (before the poll loop starts) propagates immediately; `cli/attach.py` catches it and maps it to exit code 2, same as `ConfigError`/`NoPipelineFoundError`/`NotFoundError`. Once inside the steady-state poll loop, though, a single bad tick is not fatal: the client has already retried transient failures internally (see [Retry](#retry) above), so reaching the engine at all means those retries were exhausted, or the error wasn't transient. Either way, `attach()` logs a warning (`logger.warning`, visible with `-v`; no visible event is emitted — this is deliberately log-only, not a new `AttachEvent` kind) and skips the tick, keeping the last known state, rather than propagating. This is capped at `MAX_CONSECUTIVE_POLL_FAILURES` (5) consecutive failures before finally giving up and re-raising — so a `--timeout`-less `attach` against a genuinely dead GitLab instance doesn't poll forever with no way to stop but Ctrl-C. The `--follow` check and a newly-followed pipeline's job fetch get the same log-and-skip treatment but **never** count toward that threshold: follow is opportunistic, so a failure there just means "no follow this tick" — the main poll for the current pipeline still gets its own independent attempt in the same tick.

`--detail` (which already-emitted event kinds a renderer shows, and — in Live mode — how much content is packed into the single status line) is deliberately **not** an engine concept — the engine always emits the full stream; filtering/scaling is entirely a `render/attach.py` concern (see [Format & Render](#format--render) below).

##### ETA seam (`DurationEstimator`)

```python
class DurationEstimator(Protocol):
    def estimate_remaining(self, pipeline: Pipeline, jobs: list[Job]) -> timedelta | None: ...
```

There's no ETA field in the GitLab API. `attach()` calls an injected `estimator` (default `NullEstimator`, which always returns `None`) inside `_context()` — this has to happen in the engine, not a renderer, because it's the only place real `Pipeline`/`Job` domain objects exist; the result is exposed to renderers as `AttachEvent.eta_seconds`, keeping the estimator itself out of the render layer entirely. v1 ships no estimation logic; a future estimator (preferred: backed by Datadog CI Visibility historical durations, rather than querying GitLab pipeline history) plugs in here with no change to `attach()` or the renderers.

---

### Cache

`src/ddgl/cache/` — namespace-aware, multi-backend cache.

#### Opening the cache

`Cache` is a singleton. Open it once at startup, close it on exit:

```python
from pathlib import Path
from ddgl.cache import Cache

with Cache.open(Path("~/.cache/ddgl").expanduser()) as cache:
    ...
```

Pass `bypass=True` to disable reads (writes still go through — this is what `--no-cache` does):

```python
with Cache.open(cache_dir, bypass=True) as cache:
    ...
```

#### Namespaces

Each namespace maps to a dedicated backend and enforces a fixed key structure:

| Namespace               | Backend       | Key                              | Stored as                        |
| ----------------------- | ------------- | -------------------------------- | -------------------------------- |
| `CacheNS.PROJECTS`      | JSON          | `(git_root_path,)`               | In-memory dict, flushed on close |
| `CacheNS.TOKENS`        | JSON          | `(gitlab_url,)`                  | In-memory dict, flushed on close |
| `CacheNS.API_RESPONSES` | KV SQLite     | `(request_hash,)`                | TEXT rows                        |
| `CacheNS.OBJECTS`       | Struct SQLite | `(table, project_id, object_id)` | One table per struct type        |
| `CacheNS.LOGS`          | Text files    | `(job_id,)`                      | One file per job                 |

#### Reading and writing

Access a namespace via `cache[CacheNS.X]`. TTL is required on all writes.

```python
# flat key
hit = cache[CacheNS.API_RESPONSES]["abc123"]
cache[CacheNS.API_RESPONSES].set("abc123", json_str, ttl=30.0)

# nested key — chain subscripts
pipeline = cache[CacheNS.OBJECTS]["pipelines"]["proj42"][pipeline_id]
cache[CacheNS.OBJECTS].set(("pipelines", "proj42", pipeline_id), obj, ttl=604800.0)

# bind a sub-namespace and reuse it
pipelines = cache[CacheNS.OBJECTS]["pipelines"]["proj42"]
hit = pipelines[pipeline_id]
pipelines.set(pipeline_id, obj, ttl=604800.0)
```

#### Bulk reads

`StructSqliteBackend` (used by `CacheNS.OBJECTS`) supports fetching all rows matching a key prefix in a single query — used by `core/pipeline.py` and `core/jobs.py` for the bulk-fetch optimization:

```python
all_pipelines = cache[CacheNS.OBJECTS]["pipelines"]["proj42"].get_all(cls=Pipeline)
```

`get_all` raises `NotImplementedError` on backends that don't support it.

#### Backends

| Backend               | File       | Notes                                                                                    |
| --------------------- | ---------- | ---------------------------------------------------------------------------------------- |
| `JsonBackend`         | `*.json`   | In-memory; flushed atomically on close or process exit                                   |
| `KvSqliteBackend`     | `*.sqlite` | `kv` table with `key TEXT PRIMARY KEY, value TEXT, expires_at REAL`                      |
| `StructSqliteBackend` | `*.sqlite` | One table per `msgspec.Struct` type; schema-hash versioned (auto-drops on schema change) |
| `TextFileBackend`     | directory  | One file per key; TTL in sidecar `.expires` file; GC on open                             |

#### Cache TTLs

Defined in `constants.py`:

| Constant                      | Value  | Used for                     |
| ----------------------------- | ------ | ---------------------------- |
| `CACHE_TTL_FINISHED_PIPELINE` | 1 week | Finished pipeline objects    |
| `CACHE_TTL_FINISHED_JOB`      | 1 week | Finished job objects + logs  |
| `CACHE_TTL_API_PIPELINE_LIST` | 10 s   | Pipeline list pages          |
| `CACHE_TTL_API_PIPELINE`      | 30 s   | Single pipeline API response |
| `CACHE_TTL_API_JOB_LIST`      | 15 s   | Job list pages               |
| `CACHE_TTL_API_JOB`           | 60 s   | Single job API response      |
| `CACHE_TTL_TOKEN`             | 1 h    | Resolved GitLab tokens       |

---

### CLI

`src/ddgl/cli/` — Click command wrappers over `core/`.

#### Entry point

`cli/__init__.py` defines the `main` group. Global state (verbosity, `--no-cache`, `--yes`) is passed through `ctx.obj` to subcommands.

```python
@click.group()
@click.option("-v", "--verbose", count=True)
@click.option("--no-cache", is_flag=True)
@click.pass_context
def main(ctx, verbose, no_cache):
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["no_cache"] = no_cache
```

#### Shared option decorators (`cli/_options.py`)

Reusable decorator bundles to keep command signatures consistent:

- `@pipeline_resolution_options` — adds `--ref`, `--pipeline`, `--depth`
- `@job_filter_options` — adds `-f/--failed`, `--stage`, `--name`
- `@output_options` — adds `--json`, `--no-pager`

#### Command pattern

Every command follows the same structure:

```python
@jobs.command("list")
@pipeline_resolution_options
@job_filter_options
@output_options
@click.pass_context
def jobs_list(ctx, ref, pipeline, depth, failed, stage, name, json, no_pager):
    no_cache = ctx.obj["no_cache"]
    try:
        result = asyncio.run(_jobs_list_async(
            ref=ref, pipeline_id=pipeline, depth=depth,
            failed_only=failed, stage=stage, name_pattern=name,
            no_cache=no_cache,
        ))
    except (ConfigError, NoPipelineFoundError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    # render result
```

The async implementation opens the cache and client, calls `core/`, and returns raw domain objects. Rendering happens in the sync wrapper.

---

### TUI

`src/ddgl/tui/` — Textual application for interactive browsing.

The TUI is a separate presentation layer. It imports from `core/` and `client.py` directly — not from `cli/`.

#### `app.py` — PipelineViewer

The main `App` subclass. Reactive state:
- `pipeline: reactive[Pipeline | None]` — currently displayed pipeline

Layout:
- **Left column**: `PipelineInfoPanel` (metadata) + `PipelineListPanel` (sidebar)
- **Center**: `JobListPanel` (DataTable)
- **Bottom**: `FuzzySearchInput` + `FilterButton` (status, stage) + `SortButton`

Jobs are loaded via a `@work(exclusive=True)` worker that streams from `core.jobs.list_jobs()` and appends to the table incrementally.

#### `screens/job_detail.py` — JobDetailScreen

Full-screen modal with:
- **Left panel**: job metadata (`name`, `stage`, `status`, `duration`, runner info, failure reason)
- **Right panel**: tabbed content
  - **Log** — `RichLog` widget with section support; log search via `/`
  - **Deps** — `JobDAGPanel` (dependency tree)
  - **History** / **Tests** — placeholders

Log rendering converts the `Trace` IR into `Rich.Text` objects. Sections are individually collapsible; `t` toggles all at once.

#### Search (`tui/search.py`)

`apply_search(jobs, spec)` filters a list of jobs against a `FilterSpec`. Special tokens in the search string (`status:failed`, `stage:build`) are extracted and routed to the appropriate filter; the remainder is matched fuzzy or regex against the job name.

#### Widget overview

| Widget              | File                | Purpose                                |
| ------------------- | ------------------- | -------------------------------------- |
| `JobListPanel`      | `job_list.py`       | Sortable DataTable of jobs             |
| `PipelineListPanel` | `pipeline_list.py`  | Sidebar of recent pipelines for a ref  |
| `PipelineInfoPanel` | `pipeline_info.py`  | Title, ref, status, job stats          |
| `JobDAGPanel`       | `job_dag.py`        | Dependency tree (Textual Tree widget)  |
| `FuzzySearchInput`  | `search_bar.py`     | `/`-activated search with regex toggle |
| `FilterButton`      | `filter_buttons.py` | Multi-select dropdown (status, stage)  |
| `SortButton`        | `filter_buttons.py` | Cycle sort mode                        |
| `HelpModal`         | `help.py`           | Keybindings overlay                    |

---

### Format & Render

#### `format/` — trace parsing

Parses raw GitLab CI job log text (with GitLab's section markers, ANSI codes, and stream markers) into the `Trace` IR defined in `model/trace.py`.

Public API in `format/__init__.py`:
- `parse_trace(text: str) -> Trace` — parse raw log into IR
- `format_trace(text: str, options: TraceOptions) -> str` — parse + render to string
- `to_json(trace: Trace) -> str` — serialize IR to JSON

`TraceOptions` (in `_options.py`) controls rendering: `raw`, `sections`, `strip`, `timestamps`, `highlight`, `color`.

#### `render/` — Rich output for CLI

Separate from `format/`. Provides `render_pipeline_table`, `render_job_table`, `render_job_detail`, etc. — functions that take domain objects and return Rich renderables, used by CLI commands.

`_console.py` holds the shared `console` and `err_console` instances.

`render/attach.py` is a bit different: it consumes an `AsyncIterator[AttachEvent]` (see `core/attach.py` above) rather than a single already-fetched domain object, since it's rendering a live stream. Two entry points, both returning the final `result` event so the CLI can map it to an exit code:

- `render_lines(events, as_json=..., detail=...)` — the default non-TTY output and `--json`'s JSONL are the *same* renderer; `as_json` is a boolean on the same consumption loop, not a separate function, since it's the same event stream just formatted differently, and (deliberately) bypasses `--detail` filtering entirely — JSONL always carries every event, full fidelity. Otherwise each event is passed through `_visible_at(event, detail)` before being formatted: `result` always shows; `none` shows nothing else; `minimal` additionally shows summary-shaped events (`snapshot`/`poll`/`heartbeat`); `normal` additionally shows `pipeline`/`switched` unconditionally (rare/low-noise, unlike `job`) plus `job` transitions that reach a terminal status (`success`/`failed`/`canceled`/`skipped`) — the created→running/running→pending blips that dominate on a large pipeline are the one thing gated by status at this level; `full` shows everything unfiltered. The engine, not this renderer, owns poll boundaries: after a changed tick it emits one `poll` event after all transitions, carrying the rollup (job counts, failure count, current stage); on a quiet tick it emits only the opt-in `heartbeat` event. This keeps every transition line concise without relying on renderer-side event tracking or formatting switches. Prints with `markup=False, highlight=False, soft_wrap=True` — literal `[TAG]` text must never be read as Rich markup, and a long line (a long job name, or a JSONL object) must never be word-wrapped across multiple physical lines.
- `render_live(events, detail=...)` — a Rich `Live` single redrawing line for the human TTY case. Live mode has no discrete lines to filter, so `--detail` instead scales how much content `_live_markup` packs into that one line: `none` is the bare spinner with no text until the final state; `minimal` is id + ref + job counts + elapsed only; `normal` (default) adds current stage + ETA; `full` is the same as `normal` but names failed jobs instead of just counting them. A `switched` event is always printed as a one-off line above the `Live` region regardless of `--detail`, including at `none` — a human watching shouldn't be left wondering why the pipeline id silently changed.

---

## Testing

Tests mirror the source tree: `tests/foo/test_bar.py` covers `src/ddgl/foo/bar.py`. One test file per source module.

### Key fixtures (`conftest.py`)

```python
TEST_CONFIG = Config(
    gitlab_url="https://gitlab.example.com",
    private_token="test-token",
    project_id="my-group/my-project",
)

@pytest.fixture()
def mock_api() -> Iterator[respx.MockRouter]:
    """respx mock pre-configured for the test GitLab base URL."""
    with respx.mock(base_url=TEST_CONFIG.api_url) as router:
        yield router

@pytest.fixture()
async def client(mock_api) -> GitLabClient:
    async with GitLabClient(TEST_CONFIG) as c:
        yield c
```

### FakeCache (`tests/core/_stubs.py`)

An in-memory stub that mirrors the `Cache` / `_NamespaceProxy` interface without hitting disk. Defined locally in the test tree — not a re-export of a production type.

```python
cache = FakeCache()
cache[CacheNS.OBJECTS].set(("pipelines", "proj", 42), pipeline, ttl=1000)
assert cache[CacheNS.OBJECTS]["pipelines"]["proj"][42] == pipeline
```

### HTTP mocking with respx

All client tests use `respx` to intercept httpx requests:

```python
async def test_fetches_from_api_on_miss(client, mock_api):
    mock_api.get("/projects/my-group%2Fmy-project/pipelines/42").mock(
        return_value=Response(200, json=_pipeline_payload(42))
    )
    pipeline = await get_pipeline(client, 42)
    assert pipeline.id == 42
```

### Testing Textual widgets and screens

Two Textual-specific gotchas that don't show up until a test asserts on
something Textual itself controls, rather than on `core`/`client`-level
state:

- **Don't assert identity on a `reactive` field.** Textual's `reactive`
  descriptor skips the underlying assignment (and any `watch_*` call) when
  the new value is `==` the old one, unless declared with
  `always_update=True`. Domain types are `msgspec.Struct`, so two
  independently-fetched objects with identical field values compare equal
  — `app.pipeline is not old_pipeline` after a refresh can fail even
  though the refresh genuinely ran, because Textual decided there was
  nothing to update. Assert on an observable side effect instead (a call
  count on the fake client, a field on the resulting object), not on
  whether the reactive happened to get reassigned.

- **A message handler patched onto an instance after construction is
  never dispatched.** Textual resolves `on_<Message>` handlers from the
  class, computed once, so `monkeypatch.setattr(app, "on_foo_bar", fn)` is
  silently never called. Define a small `App`/`Screen` subclass with the
  handler as a real method instead.

### Running tests

```bash
uv run pytest -v              # all tests
uv run pytest tests/core/     # one directory
uv run pytest -k test_cache   # by name pattern
```
