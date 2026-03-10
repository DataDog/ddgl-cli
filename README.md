# ddgl

Terminal-based GitLab client.

## Setup

```bash
uv sync
```

Authentication is resolved in order:
1. `GITLAB_TOKEN` environment variable
2. `ddtool auth gitlab token` subprocess fallback

Optionally set `GITLAB_PROJECT_ID` (e.g. `my-group/my-project`) or let ddgl detect it from the git remote.

## Using the async client

`GitLabClient` is an async context manager backed by `httpx`:

```python
from ddgl.config import load_config
from ddgl.client import GitLabClient

config = await load_config()

async with GitLabClient(config) as client:
    pipeline = await client.get_pipeline(12345)
    print(pipeline.status, pipeline.ref)
```

### Method naming convention

Public methods on the client follow a naming convention that tells you how pagination is handled:

| Prefix | Returns | Pagination | Example |
|---|---|---|---|
| `get_` | `T` | None (single object) | `get_pipeline(id)` |
| `fetch_` | `Page[T]` | Single page | `fetch_pipelines(ref="main")` |
| `iter_` | `AsyncIterator[Page[T]]` | Streams all pages | `iter_pipelines()` |
| `get_all_` | `list[T]` | Exhausts all pages | `get_all_jobs(pipeline_id)` |

#### `fetch_` -- single page

Returns a `Page[T]` containing the items and pagination metadata. Use this when you only need the first page or want manual control over pagination.

```python
page = await client.fetch_pipelines(ref="main", per_page=10)
for p in page.items:
    print(p.id, p.status)
if page.has_next:
    print(f"Page {page.page} of {page.total_pages}")
```

#### `iter_` -- stream pages

Async iterator that yields `Page[T]` objects until all pages are consumed. Use this for streaming large result sets (e.g. in a TUI).

```python
async for page in client.iter_pipelines(ref="main"):
    for p in page.items:
        print(p.id, p.status)
```

#### `get_all_` -- flat list

Exhausts pagination and returns a flat `list[T]`. Use this when the result set is bounded (e.g. jobs for a single pipeline).

```python
jobs = await client.get_all_jobs(pipeline_id=12345)
failed = [j for j in jobs if j.has_failed]
```

#### Pagination limit

All paginating methods (`iter_`, `get_all_`) raise `PaginationLimitError` if they exceed `MAX_PAGES` (defined in `constants.py`, default 50). This is a safety net -- if you hit it, something is probably wrong.

## Caching

`ddgl` uses a layered, namespace-aware cache stored under `~/.cache/ddgl/` (or whichever directory is passed to `Cache.open`).

### Opening the cache

`Cache` is a singleton context manager. Open it once at startup and close it on exit:

```python
from pathlib import Path
from ddgl.cache import Cache

with Cache.open(Path("~/.cache/ddgl").expanduser()) as cache:
    ...
```

Pass `bypass=True` to disable reads (writes still go through — useful for force-refresh):

```python
with Cache.open(cache_dir, bypass=True) as cache:
    ...
```

### Namespaces

Each namespace has a dedicated backend and key shape, declared in `CacheNS`:

| Namespace | Backend | Key shape | Stored as |
|---|---|---|---|
| `CacheNS.PROJECTS` | JSON file | `(git_root_path,)` | In-memory dict, flushed on close |
| `CacheNS.TOKENS` | JSON file | `(gitlab_url,)` | In-memory dict, flushed on close |
| `CacheNS.API_RESPONSES` | SQLite KV | `(request_hash,)` | `TEXT` rows |
| `CacheNS.OBJECTS` | SQLite structs | `(table_name, project_id, object_id)` | One table per struct type |
| `CacheNS.LOGS` | Text files | `(job_id,)` | One file per job |

### Reading and writing

Access a namespace via `cache[CacheNS.X]`, then index into it with the key components. TTL is always required on writes.

```python
# flat namespace (1 component)
hit = cache[CacheNS.API_RESPONSES]["abc123"]
cache[CacheNS.API_RESPONSES].set("abc123", json_str, ttl=30.0)

# nested namespace — chain subscripts or use a tuple shortcut
pipeline = cache[CacheNS.OBJECTS]["pipelines"]["proj42"][pipeline_id]
cache[CacheNS.OBJECTS].set(("pipelines", "proj42", pipeline_id), obj, ttl=3600.0)
```

Intermediate subscripts return a proxy, so you can bind a sub-namespace and reuse it:

```python
pipelines = cache[CacheNS.OBJECTS]["pipelines"]["proj42"]
hit = pipelines[pipeline_id]
pipelines.set(pipeline_id, obj, ttl=3600.0)
```

### Bulk reads (`get_all`)

`StructSqliteBackend` (used by `CacheNS.OBJECTS`) supports fetching all rows that match a key prefix:

```python
# all pipelines for a project
all_pipelines = cache[CacheNS.OBJECTS]["pipelines"]["proj42"].get_all(cls=Pipeline)

# all objects in a table regardless of project
everything = cache[CacheNS.OBJECTS]["pipelines"].get_all(cls=Pipeline)
```

`get_all` raises `NotImplementedError` on backends that don't support bulk reads.

### Backends

| Backend | File | Notes |
|---|---|---|
| `JsonBackend` | `*.json` | In-memory; flushed atomically on `close()` or process exit |
| `KvSqliteBackend` | `*.sqlite` | Simple key→text SQLite store |
| `StructSqliteBackend` | `*.sqlite` | One table per `msgspec.Struct` type; schema-hash versioned |
| `TextFileBackend` | directory | One file per key; TTL tracked via sidecar `.expires` files |

All backends create their storage file/directory automatically. Expired entries are pruned on open (GC). If a struct schema changes between versions, the stale table is dropped and recreated automatically.

## CLI

```bash
ddgl pipelines --ref main -n 20
ddgl logs <job_id>
```

## Development

```bash
uv run pytest -v
uv run ruff check src/ tests/
```
