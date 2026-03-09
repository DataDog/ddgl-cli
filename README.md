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

config = load_config()

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
