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
