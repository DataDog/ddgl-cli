# ddgl

Terminal-based GitLab CI client — browse pipelines, stream logs, and triage failures from your terminal.

<!-- TODO: screenshot / asciinema of `ddgl viz` -->

## Features

- **Branch-aware** — auto-detects your current git branch and resolves the latest pipeline with no arguments
- **Interactive TUI** (`ddgl viz`) — full pipeline browser with job list, status/stage filters, fuzzy or regex search, and matrix job grouping
- **Blocking wait** (`ddgl attach`) — block until a pipeline finishes, streaming progress; a live status line for humans, an append-only/JSONL event stream for scripts and coding agents
- **Job detail view** — streaming log with collapsible sections, dependency graph (DAG), and keyboard navigation
- **Smart log formatting** — ANSI colors preserved, sections folded, optional timestamps, syntax highlighting
- **Scripting-friendly** — `--json` output on every command, pipe-friendly, `--no-cache` for force-refresh
- **Local caching** — finished pipelines and logs cached for one week; repeated queries are instant

## Installation

Requires Python ≥ 3.12.

```bash
# Recommended — uv
uv tool install git+https://github.com/ddoghq-sandbox/ddgl

# Or pip
pip install git+https://github.com/ddoghq-sandbox/ddgl
```

## Authentication & Configuration

Set `GITLAB_TOKEN` to a [personal access token](https://docs.gitlab.com/ee/user/profile/personal_access_tokens.html) with `read_api` scope. If the variable is not set, `ddtool auth gitlab token` is tried automatically (Datadog internal).

The project is auto-detected from the `origin` git remote when run inside a repository. You can override any setting with environment variables:

| Variable            | Default             | Description                                        |
| ------------------- | ------------------- | -------------------------------------------------- |
| `GITLAB_TOKEN`      | —                   | Personal access token (required)                   |
| `GITLAB_URL`        | `gitlab.ddbuild.io` | GitLab instance base URL                           |
| `GITLAB_PROJECT_ID` | auto-detected       | Project path, e.g. `my-group/my-project`           |
| `DDGL_LOG_LEVEL`    | `WARNING`           | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Quick Start

```bash
# Latest pipeline for current branch
ddgl pipelines get

# Open interactive TUI
ddgl viz

# List failed jobs for the latest pipeline on the current branch
ddgl jobs list --failed

# Show the logs for a specific job
ddgl logs --job <id>
```

## Common Workflows

### Check pipeline status

```bash
ddgl pipelines get                  # latest pipeline on current branch
ddgl pipelines get --ref main       # specific branch
ddgl pipelines list -n 10           # last 10 pipelines
ddgl pipelines list --scope running # only running pipelines
```

### Triage failed jobs

```bash
ddgl jobs list --failed
ddgl jobs list --failed --stage build
ddgl jobs list --name "lint.*"      # regex filter on job name
ddgl jobs get --failed              # full details for all failed jobs
```

### Read job logs

```bash
ddgl logs --failed                  # all failed logs in current pipeline
ddgl logs --job 12345               # single job by ID
ddgl logs --job 12345 --raw         # skip formatting
ddgl logs --job 12345 --timestamps  # include ISO timestamps
ddgl logs --job 12345 --strip       # strip ANSI codes (plain text)
ddgl logs --failed --stage test     # failed logs filtered to one stage
```

### Save or pipe output

```bash
ddgl logs --job 12345 --output /tmp/build.log
ddgl logs --job 12345 --output /tmp/logs/      # one file per job (already-existing directory)
ddgl logs --failed --strip --output /tmp/logs/ # plain-text dump of all failed logs
ddgl jobs list --failed --json | jq '.[].name'
ddgl pipelines list --json | jq '.[] | select(.status == "failed") | .id'
```

### Format a saved trace

```bash
ddgl format /path/to/trace.log
cat trace.log | ddgl format -
```

### Force-refresh (bypass cache)

```bash
ddgl --no-cache pipelines get
ddgl --no-cache logs --failed
```

### Walk back through commit history

By default ddgl searches the last 10 commits for a pipeline. Increase `--depth` when working on a branch with many commits since the last pipeline run:

```bash
ddgl pipelines get --depth 50
ddgl jobs list --failed --depth 50
```

## Interactive TUI (`ddgl viz`)

```bash
ddgl viz                     # current branch
ddgl viz --ref feature/foo   # specific branch
ddgl viz --pipeline 98765    # specific pipeline ID
```

**Layout**: pipeline list sidebar (left) · job table (center) · filter bar and footer (bottom).

**Search**: fuzzy match by default. Toggle regex with the `.*` button. Use special tokens to combine filters:

```
status:failed stage:build lint    # failed jobs in "build" stage matching "lint"
```

### Keybindings — main view

| Key      | Action                               |
| -------- | ------------------------------------ |
| `/`      | Focus search                         |
| `Ctrl+K` | Clear search                         |
| `s`      | Cycle sort: Stage → A–Z → Start time |
| `Space`  | Expand / collapse matrix job group   |
| `r`      | Refresh pipeline and jobs            |
| `p`      | Switch pipeline (focus sidebar)      |
| `o`      | Open job or pipeline in browser      |
| `?`      | Show help                            |
| `q`      | Quit                                 |

### Keybindings — job detail (Enter)

| Key                 | Action                                      |
| ------------------- | ------------------------------------------- |
| `/`                 | Search in log                               |
| `n` / `N`           | Next / previous match                       |
| `t`                 | Toggle all log sections (collapse / expand) |
| `Ctrl+↑` / `Ctrl+↓` | Fast scroll                                 |
| `Escape` / `q`      | Close                                       |

**Tabs**: Log · Deps (dependency graph) · History *(coming soon)* · Tests *(coming soon)*

## Blocking on a Pipeline (`ddgl attach`)

Blocks until a pipeline reaches a terminal state, streaming progress as it goes. Resolves a pipeline the same way as every other command (`--ref` / `--pipeline` / `--depth`), and by default waits for one to appear if you attach right after pushing.

```bash
ddgl attach                       # current branch — waits if no pipeline exists yet
ddgl attach --pipeline 98765      # pin a specific pipeline
ddgl attach --timeout 300         # give up after 5 minutes (exit 124) instead of blocking forever
ddgl attach --follow              # switch to a newer pipeline on the ref if one appears (e.g. a re-push)
```

**Output** auto-detects the audience, like every other command's `console.is_terminal` + `--json` behavior:

| Mode | When | What you get |
| --- | --- | --- |
| **Live** | stdout is a TTY, or `--live` | A single redrawing status line: current stage, job counts, elapsed time |
| **Lines** | stdout isn't a TTY (default), or `--plain` | Append-only, human-readable lines — one per event, always ending in a `[FINAL]` line with the outcome |
| **JSONL** | `--json` (forced even in a TTY) | One JSON object per event, same information as Lines mode |

`--detail {none,minimal,normal,full}` controls how much shows up in **Lines mode only** (job-level transitions, extra failure detail) — Live mode's single status line and `--json`'s JSONL always include everything. `--heartbeat` adds a tally line on poll ticks where nothing changed, useful for keeping a long wait visibly alive.

**Exit codes** follow the GNU `timeout` convention, so shell scripts and CI-babysitting agents can branch on them directly:

| Code | Meaning |
| --- | --- |
| `0` | Pipeline succeeded |
| `1` | Pipeline failed or was canceled |
| `2` | Unexpected/config error |
| `124` | `--timeout` elapsed while the pipeline was still running |

```bash
ddgl attach && ./deploy.sh                  # only deploy on success
ddgl attach --pipeline 98765 --timeout 300  # bounded wait; loop/re-invoke on exit 124
```

## Global Options

These flags are available on all commands:

| Flag              | Description                                                 |
| ----------------- | ----------------------------------------------------------- |
| `--ref <ref>`     | Git ref (branch, tag, or SHA); defaults to current branch   |
| `--pipeline <id>` | Pin a specific pipeline by ID                               |
| `--depth <n>`     | Commits to walk when searching for a pipeline (default: 10) |
| `--no-cache`      | Bypass cache reads (writes still populate the cache)        |
| `--json`          | JSON output                                                 |
| `--no-pager`      | Disable the pager                                           |
| `-v` / `-vv`      | Verbose / very verbose output                               |
| `-y` / `--yes`    | Skip confirmation prompts                                   |

Every command and subcommand accepts `--help` for the full option list:

```bash
ddgl --help
ddgl logs --help
ddgl jobs list --help
```

## Roadmap

- **Job history tab** — same job across recent pipelines for flakiness detection
- **Test results** — parsed test output in job detail view
- **YAML-based dependency graph** — replace N API calls with a single `git show` + parse

## Development

```bash
uv sync
uv run pytest -v
uv run ruff check --fix
```

See [DEVELOPER.md](DEVELOPER.md) for the overall architecture and contributor guidelines.
