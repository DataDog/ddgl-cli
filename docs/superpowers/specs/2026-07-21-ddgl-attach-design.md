# `ddgl attach` — Design

## Goal

Add a `ddgl attach` command that **blocks until a CI pipeline reaches a terminal
state**, streaming progress to stdout. One code path serves two audiences:

- **Humans** — a live, redrawing single-line status (progress counts, current
  stage, an ETA slot that stays empty in v1).
- **Agents** (e.g. a future `/babysit-pr` skill) — an append-only,
  machine-readable event stream, a self-sufficient final result line, and a
  meaningful exit code, so the agent can wait for a pipeline then act on the
  outcome (push fixes, retry jobs, etc.).

Non-goals for v1: desktop notifications, ETA calculation, job retrying, any
daemon/background process, any local session/retry state.

## Core principles

- **Block-and-stream.** The whole point of `attach` is to block. There is no
  daemon and no status file; if that were acceptable, a different mechanism
  (GitLab webhooks, a notification job, a hypothetical `ddgl set-alarm`) would
  be used instead.
- **Stateless.** `attach` holds no durable state and has no concept of
  "retrying" or "resuming". Every invocation behaves identically: resolve →
  snapshot → stream until terminal/timeout. GitLab (through the existing
  client + cache) is the sole source of truth. This is what makes the
  loop-based fallback (below) work for free.
- **Layer separation.** Engine logic lives in `core/`, with no knowledge of
  Click or Rich. `cli/` and `render/` are thin presentation wrappers. (See
  `DEVELOPER.md`.)
- **Self-sufficient final output.** Because several harnesses only surface a
  command's output *after it exits* (see "Harness landscape"), the final
  buffered output plus the exit code must convey the complete outcome on their
  own. Mid-run streaming is a bonus, never a correctness requirement.

## Harness landscape (why the design is shaped this way)

`attach` must work when invoked by a coding agent through its shell tool. Those
tools impose timeouts and differ in whether they surface incremental output.
Research findings (2026-07):

| Harness | Default shell timeout | Long-run mechanism | Sees mid-run stdout? |
| --- | --- | --- | --- |
| **Claude Code** | 120s (`BASH_DEFAULT_TIMEOUT_MS`), max 600s (`BASH_MAX_TIMEOUT_MS`) | `run_in_background` + `Read` output file; auto-backgrounds on timeout; **`Monitor` tool** | Yes, in background / `Monitor` |
| **Codex** | 10s classic; unified-exec yields a `session_id`, poll via `write_stdin` | unified exec polling (default on non-Windows) | Yes, between polls |
| **Pi** | none (never times out) | blocks to completion; no background bash (by design) | No (buffered until exit) |
| **OpenCode** | only experimental `OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS` | none for shell (background subagents exist but can't poll shell output) | No (buffered until exit) |

Consequences baked into this design:

- **Claude Code golden path = the `Monitor` tool.** `Monitor` is purpose-built:
  its docs list "Poll a PR or CI job and report when its status changes." It
  runs a command in the background and feeds **each stdout line** back to the
  agent as it arrives. Crucially, **`Monitor` reports stdout lines only — it
  does not parse exit codes.** Therefore the terminal outcome must appear as a
  **stdout line** (the final result line), not solely via the exit code.
- **OpenCode / Pi fallback = loop the command.** No background/poll exists, so a
  skill runs `attach` as a single blocking foreground call bounded by
  `--timeout`; if the harness kills it, the skill re-invokes. Because `attach`
  is stateless and always snapshots-then-streams, re-invocation Just Works.
- **The exit contract must stand alone.** A pure-foreground harness that only
  sees the final buffer still receives: snapshot → transitions → final result
  line → exit code. Nothing depends on mid-run observation.

## No push API — polling is the only option

There is **no push/subscribe path for pipeline/job status reachable from a local
CLI.** Webhooks require a public HTTP endpoint (unreachable behind NAT); GitLab
GraphQL exposes no CI-status subscription over the public API; the only
streaming endpoint is the job *trace* (log text, not status). Every CI-watching
tool polls, and so does the existing TUI (`set_interval`). `attach` therefore
polls the REST API on a fixed cadence.

## CLI surface

```
ddgl attach [--ref REF | --pipeline ID] [--depth N]
            [--interval SECONDS] [--heartbeat/--no-heartbeat]
            [--detail none|minimal|normal|full]
            [--no-wait] [--follow]
            [--timeout SECONDS]
            [--json] [--plain] [--live]
```

Resolution flags (`--ref`, `--pipeline`, `--depth`) mirror
`pipeline_resolution_options` and reuse `resolve_pipeline`.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--interval` | `10` | Poll cadence in seconds. Sole cadence knob: controls both change-detection and heartbeat frequency. |
| `--heartbeat / --no-heartbeat` | off | When on, emit a tally line on poll ticks where **nothing changed** (keeps the stream "alive"). Locked to `--interval`. |
| `--detail` | `normal` | Controls the *content* of event lines: `none` (pipeline-level only), `minimal`, `normal` (job transitions), `full` (adds runner/reason detail). |
| `--no-wait` | off | If no pipeline exists yet, error immediately instead of waiting for one to appear. |
| `--follow` | off | If a newer pipeline for the same ref appears, switch to following it, emitting a warning event. |
| `--timeout` | none | Max seconds to block. On elapse while still running, exit `124`. |
| `--json` | off | Force JSONL output (one JSON object per event) even in a TTY. |
| `--plain` | auto | Force append-only line output (overrides TTY auto-detect). |
| `--live` | auto | Force the redrawing TTY view (overrides non-TTY auto-detect). |

**Pre-start behavior.** Default = wait for a pipeline to appear (bounded by
`--timeout`), critical for "push then attach". `--no-wait` = require an existing
pipeline. `--follow` = auto-follow to newer pipelines on the ref.
(Open question: whether `--follow` should be the *default*. Left off by default
in v1; revisit.)

## Output modes

Selected by TTY auto-detection with explicit overrides (mirrors the existing
`console.is_terminal` + `--json` pattern):

| Mode | Trigger | Content |
| --- | --- | --- |
| **Live (human)** | stdout is a TTY, or `--live` | Exit code + Rich `Live` **single redrawing line**: jobs complete/total, current stage, ETA slot (empty in v1), spinner. |
| **Lines (default non-TTY)** | stdout not a TTY, or `--plain` | Exit code + append-only, machine-readable **non-JSON** lines, one per event. |
| **JSONL** | `--json` (forced even in TTY) | Exit code + one JSON object per line (the serialized `AttachEvent`). |

`--heartbeat`, `--detail`, `--no-wait`, `--follow` affect *behavior* or the
*content* of lines, **not** the output format.

### Final result line

Because CC `Monitor` never sees the exit code, the **last line emitted is always
a machine-readable result**, present in every mode. In lines mode it is a
`[FINAL]`-tagged human-readable line an agent can grep for regardless of
`--detail` (e.g. `[12:31:57][FINAL] Pipeline #918342 FAILED. Failed jobs: build:unit, lint:ruff`);
in JSONL mode it is the final `AttachEvent(kind="result")`. It conveys at least:
pipeline id, terminal status, list of failed jobs, duration, and the stop reason
(`terminal` | `timeout`).

## Exit codes

Maps pipeline outcome, following the GNU `timeout` convention agents already
know:

| Code | Meaning |
| --- | --- |
| `0` | Pipeline succeeded. |
| `1` | Pipeline failed or canceled. |
| `2` | Unexpected error (usage, config, unhandled). |
| `124` | `--timeout` elapsed while the pipeline was still running (caller may re-invoke to resume). |

## Components

Layer placement per `DEVELOPER.md` (`core/` has no UI deps; `cli/` + `render/`
are thin).

### `model/attach.py` — one event struct

A single `msgspec.Struct`, `kind`-tagged (no class hierarchy):

```python
class AttachEvent(msgspec.Struct):
    kind: Literal["snapshot", "job", "pipeline", "heartbeat", "switched", "result"]
    ts: str                          # ISO timestamp
    # optional payload fields, populated per kind:
    pipeline_id: int | None = None
    status: str | None = None        # new pipeline/job status
    old_status: str | None = None    # previous status (transitions)
    job_name: str | None = None
    job_id: int | None = None
    duration: float | None = None
    message: str | None = None       # e.g. switch warning, snapshot summary
    # snapshot / result rollups:
    jobs_total: int | None = None
    jobs_done: int | None = None
    failed_jobs: tuple[str, ...] = ()
    reason: str | None = None        # result: "terminal" | "timeout"
```

Renderers switch on `.kind`. `--json` serializes the struct directly via
`msgspec.json.encode`.

### `core/attach.py` — the engine (no UI)

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
) -> AsyncIterator[AttachEvent]: ...
```

Flow:

1. **Resolve / wait.** Call `resolve_pipeline`. If it raises
   `NoPipelineFoundError` and `wait_for_start`, poll until one appears or
   `timeout`. Emit the resolved pipeline id early (in the snapshot) so a skill
   can capture it and pin `--pipeline <id>` across loop iterations.
2. **Snapshot.** Fetch pipeline + all jobs, emit `kind="snapshot"` with rollup
   counts. This full-state-on-start is the universal first output; it is *not*
   retry-aware logic.
3. **Poll loop** every `interval`s, **cache-bypassed** for the running pipeline
   and its job list (so `--interval` is the true detection latency; matches the
   "only terminal objects cached durably" rule — terminal jobs are still
   *written* to cache for later `ddgl logs`/`jobs`):
   - Diff current vs last-seen pipeline status → emit `kind="pipeline"` on change.
   - Diff each job's status → emit `kind="job"` per transition (respecting
     `--detail`).
   - If `follow` and a newer pipeline id appears for the ref → emit
     `kind="switched"` (warning) and rebind to it.
   - If `heartbeat` and the tick had no change → emit `kind="heartbeat"` tally.
   - If pipeline terminal → emit `kind="result"` (reason `terminal`) and return.
   - If `timeout` elapsed → emit `kind="result"` (reason `timeout`) and return.

The engine yields events; it does not print, choose exit codes, or format.

### `core/estimate.py` — ETA seam (stubbed in v1)

```python
class DurationEstimator(Protocol):
    def estimate_remaining(
        self, pipeline: Pipeline, jobs: list[Job]
    ) -> timedelta | None: ...

class NullEstimator:
    def estimate_remaining(self, pipeline, jobs) -> None:
        return None
```

v1 wires `NullEstimator`, so the TTY line's ETA slot renders nothing. A future
`DatadogCIEstimator` (preferred over querying GitLab history) slots in here with
no change to `attach` or the renderers.

### `render/attach.py` — presentation

- `render_live(events, estimator)` — consumes the async event stream, maintains
  a Rich `Live` single redrawing line (progress, stage, ETA slot, spinner).
- `render_lines(events, *, as_json: bool)` — consumes the same stream; formats
  each event as a human-readable line, or as `msgspec.json.encode(event)` when
  `as_json=True`. JSONL is thus a boolean on the line renderer, not a separate
  renderer.
- A shared `event_to_text(event, detail)` helper maps an `AttachEvent` to its
  human line, reused by both renderers.

### `cli/attach.py` — thin wrapper

- Parse flags; open `Cache` + `GitLabClient`.
- Choose renderer: TTY (or `--live`) → `render_live`; else `render_lines`;
  `--json` forces `render_lines(as_json=True)`.
- Drive `core.attach.attach(...)`, feed events to the renderer, capture the
  final `result` event, map it to the exit code (0/1/2/124).
- Catch `ConfigError` / `NoPipelineFoundError` / `NotFoundError` → exit `2`
  (or `124` where a wait timed out), consistent with existing commands.

## Testing strategy

Mirror the source tree (`tests/core/test_attach.py`, etc.), hermetic, using the
existing `respx` mock + `FakeCache` fixtures.

- **`core/attach.py` (unit):** feed scripted API responses via `respx`; assert
  the exact `AttachEvent` sequence. Cases: happy path (snapshot → job
  transitions → success result); failure path (result reason `terminal`, status
  failed, `failed_jobs` populated); wait-for-start (no pipeline, then one
  appears); `--no-wait` raises; `--follow` switch emits `switched`; `--timeout`
  elapsed emits result reason `timeout`; heartbeat emits tally on quiet ticks;
  cache-bypass on running reads but terminal jobs written to cache.
- **`core/estimate.py` (unit):** `NullEstimator` returns `None`.
- **`render/attach.py` (unit):** given a fixed event list, assert line text per
  `--detail`; assert JSONL round-trips via `msgspec`; assert the final result
  line/sentinel is always present.
- **`cli/attach.py` (unit):** event sequence → exit code mapping (0/1/2/124);
  TTY vs non-TTY vs `--json` renderer selection.

## Implementation plan (atomic commits)

Ordered foundation → functionality → polish. Each commit stands alone with
tests.

1. `feat(model): add AttachEvent struct` — the one `kind`-tagged event struct +
   tests.
2. `feat(core): add DurationEstimator protocol and NullEstimator` — the ETA seam
   stub + test.
3. `feat(core): add attach() engine` — resolution/wait, snapshot, poll/diff
   loop, follow, heartbeat, timeout; cache-bypass semantics. Unit tests with
   `respx` + `FakeCache`. (The bulk of the work.)
4. `feat(render): add attach line + live renderers` — `render_lines(as_json)`,
   `render_live`, shared `event_to_text`. Tests on event→text and JSONL.
5. `feat(cli): add attach command` — wire flags, renderer selection, exit-code
   mapping; register in `cli/__init__.py`. Tests on exit codes + selection.
6. `docs: document ddgl attach` — README workflows + `DEVELOPER.md` note on the
   attach engine and the ETA seam.

## Deferred / open

- **`--follow` as default** — left off in v1; revisit once real usage exists.
- **Desktop notifications** — `--notify` (macOS `osascript`/`terminal-notifier`,
  Linux `notify-send`, best-effort via `shell.run`), deferred to a later
  version.
- **ETA** — architected via `DurationEstimator`; real implementation
  (Datadog CI Visibility preferred) deferred entirely.
- **Job retrying** — the client is currently read-only (no POST). Auto-retry of
  failed jobs is a separate feature; `attach` only observes.
