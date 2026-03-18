# TUI: Later Features

Features deferred from the self-audit plan (F2–F8 + U6).

---

## U6 / F6 — Open URLs in browser

- `o` on pipeline info panel: `webbrowser.open(pipeline.web_url)` when available.
- `o` on a selected job row: `webbrowser.open(job.web_url)`.
- Disable/hide the binding when the URL is empty.

Files: `pipeline_info.py`, `app.py`, `job_list.py`.

---

## F2 — Auto-collapse matrix jobs and stage groups

Matrix jobs share a base name (`job: [param]` / `job [param]`). Group them into
a single summary row with aggregate status counts and summed duration.

### Matrix detection
```python
def matrix_base_name(name: str) -> str:
    for sep in (" [", ": [", ":["):
        if sep in name:
            return name[:name.index(sep)].strip()
    return name
```

### Display model
- `_DisplayRow = JobRow(job) | GroupRow(base_name, jobs, expanded=False)`
- `GroupRow` shows `▶`/`▼` toggle, aggregate status (worst-case), job count, summed duration.
- `Enter` on a group row toggles `expanded`; individual rows appear indented below.
- When a filter/search is active, skip grouping — show all matching jobs flat.

Files: `job_list.py` (new `_DisplayRow` union, `_group_jobs()`, updated `_repopulate`).

---

## F3 — Auto-refresh for running pipelines

When `pipeline.status` is running, start a background timer (~15–30 s) that
re-calls `resolve_pipeline()` + `load_pipeline()`.

- `r` keybinding for manual refresh.
- Show "last updated HH:MM:SS" in the header subtitle.

Files: `app.py` (timer management, `r` binding, header subtitle).

---

## F4 — Job detail / log view

`Enter` on a non-group job row opens a `ModalScreen` (overlay) with:
- Job name, stage, status (coloured), failure reason, duration.
- Scrollable log via `RichLog` widget.

Fetch on demand: `get_job(client, job_id, cache=cache)` from `src/ddgl/core/jobs.py`.

Files: new `widgets/job_detail.py`, `app.py` (bind `enter`, open modal).

---

## F5 — Pipeline summary stats in info panel

After jobs load, add a one-line aggregate to `PipelineInfoPanel`:
```
✓ 34   ✗ 2   ● 1   → 5
```
Computed in `app.py` after `_load_jobs` completes; passed to the panel reactively.

Files: `pipeline_info.py` (new reactive `job_stats`), `app.py`.

---

## F7 — Pipeline switcher

`p` opens a popup listing recent pipelines for the current ref.
Selecting one calls `load_pipeline()`.

Reuses `resolve_pipeline()` from `src/ddgl/core/pipeline.py` and the
`load_pipeline()` refactor from commit 6.

Files: new `widgets/pipeline_switcher.py`, `app.py`.

---

## F8 — Help modal

`?` opens a `ModalScreen` listing all bindings, filter syntax (`status:`, `stage:`),
collapse behaviour, and sort modes.

Files: new `widgets/help_modal.py`, `app.py`.

---

## P1 — Full datetime in pipeline list

Currently `_fmt_ts` in `pipeline_list.py` extracts only `HH:MM` from the ISO
timestamp.  When browsing pipelines from yesterday or last week the time alone
is ambiguous.

### Change

Replace `_fmt_ts` with `_fmt_datetime` using `datetime.datetime.fromisoformat`:

```python
from datetime import datetime, timezone

def _fmt_datetime(ts: str) -> str:
    """Return 'Mon DD HH:MM' from an ISO-8601 timestamp, or '—'."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%-d %b %H:%M")   # e.g. "12 Mar 14:32"
    except ValueError:
        return ts[:16]
```

Widen the `At` column in `on_mount` from `width=5` to `width=11`.

Files: `widgets/pipeline_list.py`.

---

## P2 — Fast scroll (jump to top/bottom)

Users scrolling through long job logs or large job lists want a way to jump to
the extremes quickly.  On macOS, `cmd+up` / `cmd+down` are the natural gestures.
In Textual, these map to `meta+up` / `meta+down` (the terminal sends `ESC + arrow`).

### Job list (main app)
Add to `PipelineViewer.BINDINGS`:
- `meta+up` → `action_scroll_top`: calls `job_list.scroll_home()`
- `meta+down` → `action_scroll_bottom`: calls `job_list.scroll_end()`

### Job log (job detail screen)
Add to `JobDetailScreen.BINDINGS`:
- `meta+up` → scroll `RichLog` to top (`scroll_home()`)
- `meta+down` → scroll `RichLog` to bottom (`scroll_end()`)

Note: `ctrl+up` / `ctrl+down` already occupy page-scroll in `JobDetailScreen`.
`meta+up` / `meta+down` complement them with instant-jump and also work in the
main app where `ctrl` bindings don't exist yet.

Files: `tui/app.py`, `tui/screens/job_detail.py`.

---

## P3 — Regex search mode (shared across job list + log)

Add a **regex mode toggle** to all search fields.  When active, the pattern is
compiled as a regex; when inactive, it falls back to literal substring — so
casual users are unaffected.

### Shared helper: `src/ddgl/tui/search.py`

New module with a single pure function used by both the job list and the log:

```python
import re
from rich.text import Text

def apply_search(
    text: Text,
    pattern: str,
    *,
    regex: bool = False,
) -> tuple[Text, list[tuple[int, int]]]:
    """Return highlighted copy of *text* + list of (start, end) match spans.

    When *regex* is True the pattern is compiled as-is (re.IGNORECASE).
    When *regex* is False the pattern is escaped to a literal match.
    Returns (original, []) on an empty pattern.
    """
    if not pattern:
        return text, []
    flags = re.IGNORECASE
    rx = re.compile(re.escape(pattern) if not regex else pattern, flags)
    spans = [(m.start(), m.end()) for m in rx.finditer(text.plain)]
    if not spans:
        return text, []
    result = text.copy()
    for start, end in spans:
        result.stylize("reverse bold", start, end)
    return result, spans
```

### Job list (`FuzzySearchInput` / `parse_query`)

- Add a **`regex`** field to `FilterSpec` (default `False`).
- Parse a `regex:` prefix in `parse_query`: `regex:foo.*bar` sets `regex=True`.
  Alternatively, display a `[.*]` toggle button next to the search input
  (a `Button` with `variant="default"/"primary"` toggling via click).
- When filtering jobs, pass `regex=spec.regex` through to the predicate; update
  the match logic in `_make_job_predicate` (in `core/jobs.py`) to use `apply_search`.

### Log search (`JobDetailScreen`)

- Add `_search_regex: bool = False` state to the screen.
- Add a `ctrl+r` binding (`"Toggle regex"`) that flips the flag and updates the
  search input placeholder: `"Search…"` vs `"Search (regex)…"`.
- Replace the existing `_highlight_text` call in `_rebuild_search` with `apply_search`.

Files: new `tui/search.py`, `tui/screens/job_detail.py`, `tui/widgets/search_bar.py`,
`core/jobs.py`.
Tests: `tests/tui/test_search.py` — `apply_search` with literal, regex, and
invalid-regex patterns.

---

## P4 — History tab placeholder

The `JobHistoryPanel` implementation exists but makes 10 sequential `list_jobs`
calls (one per pipeline), which is slow and unreliable during a live demo.

Replace the `JobHistoryPanel` yield in `JobDetailScreen.compose()` with a
placeholder `Static` — identical style to the Tests tab — until the history
implementation is hardened:

```python
with TabPane("History", id="tab-history"):
    yield Static(
        "[dim]Job history will be available in a future update.[/dim]",
        id="history-placeholder",
    )
```

Do **not** remove `job_history.py` or its tests; just stop mounting it in the screen.

Files: `tui/screens/job_detail.py`.

---

## P5 — Collapsible log sections (interactive toggle)

The `Section` model, `collapsed` field, and `▸`/`▾` display icons are already in
place.  What's missing is an interactive binding to toggle them.

### Recommended approach: global toggle

`t` → toggle all sections between collapsed and expanded simultaneously.
Avoids per-line focus complexity (which `RichLog` doesn't natively support).

- `_all_collapsed: bool = False` flag on `JobDetailScreen`
- `action_toggle_sections()`: flip the flag, set `section.collapsed` for every
  `Section` in the trace, call `_render_log()` to redraw
- Footer binding: `t  Toggle sections`

Files: `tui/screens/job_detail.py`.
Tests: `tests/tui/test_screens/test_job_detail.py` — add tests for `_walk_trace`
with collapsed sections.
