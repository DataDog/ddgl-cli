# Pipeline List: Favorites & Background Cache Preloading

## Context

`PipelineListPanel` shows recent pipelines and lets users switch between them.
Two improvements:

1. **Favorites** — pin pipelines you want to keep an eye on across sessions.
2. **Preloading** — silently warm the job cache for pipelines visible in the list,
   so navigating to them feels instant.

---

## Feature 1 — Pinned / Favourite Pipelines

### Storage: `src/ddgl/core/favorites.py`

A lightweight JSON store at `~/.config/ddgl/favorites.json`.
Favourites are scoped to pipeline IDs only (globally unique enough per user;
project scoping can be added later if needed).

```python
class FavoritesStore:
    def __init__(self, path: Path) -> None: ...
    def toggle(self, pipeline_id: int) -> bool:
        """Add if absent, remove if present. Returns new is-favourite state."""
    def is_favourite(self, pipeline_id: int) -> bool: ...
    def all(self) -> list[int]:
        """Ordered most-recently-added first."""

    # classmethod convenience (used by CLI)
    @classmethod
    def open(cls) -> FavoritesStore:
        """Open store at the default XDG config path."""
```

File format: `{"favourites": [12345, 12344, ...]}` — simplest possible JSON.
Writes are synchronous (file is tiny); no TTL (favourites are permanent until removed).

### TUI: changes to `PipelineListPanel`

**Constructor**: add `favourites: FavoritesStore | None = None` parameter.

**Display**: favourites are shown as a **pinned section at the top** of the panel,
always visible regardless of the current ref filter.  Each favourite row has a
`★` prefix in the status column.  Below the pinned section, the regular
ref-filtered list continues.  A dim header row (`─── favourites ───`) separates
the two sections.

```
┌─ Pipelines ─────────────────────────────┐
│ [ref input]                             │
│ ─── favourites ───                      │
│ ★ ✓ success   12345  main     14:32     │
│ ★ ✗ failed    12300  release  09:15     │
│ ─────────────────────────────────────   │
│   ✓ success   12399  feat/x   13:50     │
│   ✗ failed    12398  feat/x   11:00     │
└─────────────────────────────────────────┘
```

**On mount**: call `get_pipelines(client, favourites.all(), cache=cache)` to fetch
live status for all pinned pipelines.  This is a single bulk SQLite query + any
API misses (same pattern as `core.pipeline.get_pipelines`).

**`f` key**: toggles favourite status for the currently selected row.
- Posts a `FavouriteToggled` message so the app can save and refresh the panel.
- Updates the `★` icon and moves the row into/out of the pinned section.

**Binding**: add `Binding("f", "toggle_favourite", "Favourite")` to
`PipelineListPanel.BINDINGS`.

### CLI: `src/ddgl/cli/viz.py`

Construct `FavoritesStore` and pass it through:

```python
favourites = FavoritesStore.open()
app = PipelineViewer(pipeline, client, cache, favourites=favourites)
```

`PipelineViewer.__init__` stores it and passes it to `PipelineListPanel` in `compose()`.

---

## Feature 2 — Background Cache Preloading

### When & what to preload

After `PipelineListPanel._fetch()` finishes populating the table:
- Filter visible pipelines to **terminal states only** (success / failed / canceled /
  skipped) — only these are cached by `list_jobs()`.
- Preload jobs for the **top 5 terminal pipelines** in the current list.
- Additionally, preload jobs for **all favourite pipelines** (highest priority,
  regardless of position in the list), since those are exactly the ones the user
  cares about.
- Only runs when `self._cache is not None`.

### Implementation

New `@work` method on `PipelineListPanel`:

```python
@work(exclusive=False)
async def _preload_jobs(self, pipelines: list[Pipeline]) -> None:
    """Silently warm the job cache for terminal-state pipelines."""
    terminal = [
        p for p in pipelines
        if str(p.status) in {"success", "failed", "canceled", "skipped"}
    ]
    for p in terminal:
        async for _ in list_jobs(self._client, p.id, cache=self._cache):
            pass   # consume the generator; cache is populated as a side-effect
```

Called at the end of `_fetch()`:

```python
if self._cache is not None:
    candidates = favourite_pipelines + visible_pipelines[:5]
    self._preload_jobs(list({p.id: p for p in candidates}.values()))
```

`exclusive=False` lets multiple preload workers co-exist (e.g. one for
favourites + one triggered by a ref change) without cancelling each other.

### No UI feedback

Preloading is entirely silent.  The only observable effect is that navigating
to a preloaded pipeline loads its jobs from the SQLite cache rather than hitting
the API.

---

## Implementation order

| Step | File(s) | Notes |
|------|---------|-------|
| 1 | `src/ddgl/core/favorites.py` | `FavoritesStore`, `FavoritesStore.open()` |
| 2 | `tests/core/test_favorites.py` | toggle, is_favourite, all(), round-trip JSON |
| 3 | `src/ddgl/cli/viz.py` | construct `FavoritesStore`, pass to app |
| 4 | `src/ddgl/tui/app.py` | accept `favourites` kwarg, thread to panel |
| 5 | `src/ddgl/tui/widgets/pipeline_list.py` | pinned section, `f` key, `_preload_jobs` |
| 6 | `src/ddgl/tui/app.tcss` | style for separator header rows |

One commit per step; or steps 1–2 together, 3–4 together, 5–6 together.
