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
├── config.py               Config dataclass + load_config()
├── constants.py            enums (PipelineStatus, JobStatus), TTLs
├── exceptions.py           exception hierarchy
├── git.py                  async git helpers (detect project, current branch)
├── shell.py                sync + async subprocess wrappers
│
├── model/                  domain types (mostly msgspec.Struct)
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
- `has_failed` — `status == JobStatus.FAILED`
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

#### Pagination methods

Every paginated resource exposes three variants following a consistent naming convention:

| Prefix     | Returns                  | Use when                                            |
| ---------- | ------------------------ | --------------------------------------------------- |
| `get_`     | `T`                      | fetching a single object by ID                      |
| `fetch_`   | `Page[T]`                | you only need the first page or want manual control |
| `iter_`    | `AsyncIterator[Page[T]]` | streaming large result sets                         |
| `get_all_` | `list[T]`                | result set is bounded (e.g. jobs for one pipeline)  |

All `iter_` and `get_all_` methods raise `PaginationLimitError` after `MAX_PAGES` pages (default: 50). If you hit this in practice, something has gone wrong.

#### Error handling

`_raise_for_status()` translates HTTP errors:
- `404` on the project itself → `ConfigError` (project not found / wrong ID)
- `404` on a sub-resource → `NotFoundError` (resource doesn't exist)
- Other HTTP errors → `GitLabAPIError` (has `is_retryable` property for 408/429/5xx)

#### Low-level API caching

`_get()` accepts a `ttl` parameter. If `ttl > 0` and a cache is open, the raw JSON response is stored in `CacheNS.API_RESPONSES` keyed by a hash of the path and query params. This is separate from the structured object cache managed by `core/`.

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
    name_pattern: str | None = None,
    stage: str | None = None,
) -> list[Job] | AsyncIterator[Job]
```

Overloaded: returns a `list` for sync input, `AsyncIterator` for async input. Filters compose with AND. `name_pattern` is a compiled regex.

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
| `CACHE_TTL_DDTOOL_TOKEN`      | 1 h    | Tokens issued by ddtool      |

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

### Running tests

```bash
uv run pytest -v              # all tests
uv run pytest tests/core/     # one directory
uv run pytest -k test_cache   # by name pattern
```
