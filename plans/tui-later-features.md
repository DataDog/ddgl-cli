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

~~Implemented as `meta+up`/`meta+down` but removed: `meta+` bindings do not
reliably reach Textual across macOS terminal emulators (iTerm2 default config
intercepts Option+arrow; cmd+arrow is not forwarded at all).~~

**Re-implement** once a working key combo is confirmed.  Candidates:
- `g` / `G` (vim-style) — always works but conflicts with typing in search
- `ctrl+home` / `ctrl+end` — test in target terminal first
- Configure terminal to forward a specific escape sequence and document it

The scroll actions themselves are one-liners (`scroll_home()` / `scroll_end()`);
only the binding is the open question.

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

---

## P6 — Per-section click-to-collapse in log view

`RichLog` does not support click events on individual lines (it is a read-only
scrollable widget).  To support clicking on section headers to toggle collapse,
the log tab would need to be re-implemented using a different widget:

- **Option A**: Replace `RichLog` with a `ListView` where each item is a
  `ListItem(Static(...))`.  `ListView` sends `ItemSelected` on click/enter,
  allowing per-row interaction.  Downside: `ListView` re-renders the whole
  list on changes; may be slow for large logs.

- **Option B**: Keep `RichLog` for display and overlay an invisible `DataTable`
  whose rows correspond to log lines.  Handle `RowHighlighted` events.

Option A is simpler and should be explored first.

Files: `tui/screens/job_detail.py`, possibly new `tui/widgets/log_view.py`.

---

## P7 — Log line selection and copy-paste

Users want to highlight and copy individual log lines (e.g. to paste error
messages).  This is blocked by `RichLog` not supporting selection.

Implement together with P6 (switching to `ListView`): once log lines are
`ListItem` widgets, the focused item can be copied to the clipboard via
`pyperclip` or `subprocess.run(["pbcopy"])` on macOS.

Add `c` keybinding: copy the content of the currently-focused log line.

Files: `tui/screens/job_detail.py`.
