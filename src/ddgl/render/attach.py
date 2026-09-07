# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import msgspec
from rich.console import Group, RenderableType
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from ddgl.model.attach import (
    AttachEvent,
    DetailLevel,
    HeartbeatEvent,
    JobEvent,
    PipelineEvent,
    PollEvent,
    ResultEvent,
    RetryEvent,
    SnapshotEvent,
    SwitchedEvent,
)
from ddgl.render._console import console
from ddgl.render._styles import format_duration

# ---------------------------------------------------------------------------
# Lines mode (default non-TTY, or --plain; --json switches to JSONL)
# ---------------------------------------------------------------------------

_TAG_WIDTH = 8  # pads "[FINAL]" (the widest tag) plus one space


def _tag(name: str) -> str:
    return f"[{name}]".ljust(_TAG_WIDTH)


def _hhmmss(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M:%S")
    except ValueError:
        return ts


def _pluralize_jobs(count: int) -> str:
    """"1 job" / "2 jobs"."""
    return f"{count} job" if count == 1 else f"{count} jobs"


def _final_text(event: ResultEvent) -> str:
    if event.reason == "timeout":
        if event.pipeline_id is None:
            return "Timed out waiting for a pipeline to appear."
        return f"Timed out waiting for pipeline #{event.pipeline_id} (status: {event.status})."
    status = event.status.upper() if event.status else "UNKNOWN"
    text = f"Pipeline #{event.pipeline_id} {status}."
    if event.retries:
        text += f" Auto-retried {_pluralize_jobs(event.retries)}."
    if event.failed_jobs:
        text += f" Failed jobs: {', '.join(event.failed_jobs)}"
    return text


def _poll_summary(event: AttachEvent) -> str:
    """Format the rollup emitted once after a changed poll tick."""
    if event.jobs_total is None:
        return ""
    bits = [f"{event.jobs_done}/{event.jobs_total} jobs"]
    if event.failed_jobs:
        bits.append(f"{len(event.failed_jobs)} failed")
    if event.current_stage:
        bits.append(event.current_stage)
    return " · ".join(bits)


def event_to_text(event: AttachEvent, detail: DetailLevel = DetailLevel.NORMAL) -> str:
    """Render a single AttachEvent as one human-readable, agent-greppable line.

    Does not decide whether the event should be shown at all — that's a
    --detail *filtering* concern (see _visible_at / render_lines). This only
    formats a given event once the caller has decided to show it.
    """
    ts = _hhmmss(event.ts)
    if isinstance(event, SnapshotEvent):
        ref = f" {event.ref}" if event.ref else ""
        # jobs_total is None on attach()'s first ("attached, jobs not yet
        # loaded") snapshot — see AttachEvent's docstring. Must not render
        # as the literal string "None jobs".
        jobs_part = "loading jobs…" if event.jobs_total is None else f"{event.jobs_total} jobs"
        return f"[{ts}]{_tag('INFO')}attach #{event.pipeline_id}{ref} — {event.status}, {jobs_part}"
    if isinstance(event, JobEvent):
        old = event.old_status or "new"
        line = f"[{ts}]{_tag('JOB')}{event.job_name} {old}→{event.status}"
        if event.duration is not None:
            line += f" ({format_duration(event.duration)})"
        if detail == DetailLevel.FULL and event.message:
            line += f" — {event.message}"
        return line
    if isinstance(event, RetryEvent):
        return (
            f"[{ts}]{_tag('RETRY')}{event.job_name} failed → retrying "
            f"(#{event.new_job_id}, attempt {event.attempt})"
        )
    if isinstance(event, PipelineEvent):
        return f"[{ts}]{_tag('PIPE')}{event.old_status}→{event.status}"
    if isinstance(event, PollEvent):
        return f"[{ts}]{_tag('POLL')}{_poll_summary(event)}"
    if isinstance(event, HeartbeatEvent):
        return f"[{ts}]{_tag('BEAT')}{event.jobs_done}/{event.jobs_total} jobs, {len(event.failed_jobs)} failed"
    if isinstance(event, SwitchedEvent):
        return f"[{ts}]{_tag('WARN')}{event.message}"
    if isinstance(event, ResultEvent):
        return f"[{ts}]{_tag('FINAL')}{_final_text(event)}"
    raise TypeError(f"unexpected AttachEvent subclass: {type(event).__name__}")


def _visible_at(event: AttachEvent, detail: DetailLevel) -> bool:
    """Whether `event` should be printed at the given --detail level.

    --detail controls which *already-emitted* events get shown — it never
    changes what the engine emits (see core/attach.py). Each event kind
    knows its own minimum detail level (see AttachEvent.min_detail_level);
    JobEvent's is the one that's data-dependent rather than fixed — see
    its min_detail_level property for why.
    """
    return event.min_detail_level <= detail


async def render_lines(
    events: AsyncIterator[AttachEvent], *, as_json: bool = False, detail: DetailLevel = DetailLevel.NORMAL
) -> AttachEvent:
    """Consume attach()'s event stream, printing one line per event.

    as_json=True switches every line to a serialized AttachEvent (JSONL) —
    used for --json, forced even when stdout is a TTY. Otherwise each event
    is filtered by --detail (see _visible_at) and rendered via event_to_text.
    Prints with markup/highlighting disabled (this output must never have
    its literal "[TAG]" brackets misinterpreted as Rich style markup) and
    soft_wrap enabled (a long line — e.g. --json's JSONL, or a long job
    name — must stay exactly one physical line; Rich word-wraps to terminal
    width by default, which would silently split one event across multiple
    lines).

    Returns the final `result` event so the caller can map it to an exit
    code — attach() always ends with one.
    """
    result: AttachEvent | None = None
    async for event in events:
        if isinstance(event, ResultEvent):
            result = event
        if as_json:
            console.print(
                msgspec.json.encode(event).decode(), highlight=False, markup=False, soft_wrap=True
            )
            continue
        if not _visible_at(event, detail):
            continue
        console.print(event_to_text(event, detail), highlight=False, markup=False, soft_wrap=True)
    assert result is not None, "attach() event stream ended without a result event"
    return result


# ---------------------------------------------------------------------------
# Live mode (human · TTY, or --live)
# ---------------------------------------------------------------------------


def _live_markup(event: AttachEvent, detail: DetailLevel, *, retries: int = 0) -> str:
    """Build the redrawing single-line status.

    Live mode has no discrete lines to show/hide, so --detail instead scales
    how much CONTENT is packed into the one continuously-updating line:

    none:    empty — the spinner alone, no text, until the final state (the
             most faithful reading of "only the final state" for a view
             that's otherwise always live).
    minimal: id + ref + job counts + elapsed — the bare "summary" numbers.
    normal:  + stage + eta (current full behavior).
    full:    normal, but failed jobs are spelled out by name instead of
             just a count (mirrors lines-mode's --detail full).
    """
    if detail == DetailLevel.NONE:
        return ""

    bits = [f"[dim]#{event.pipeline_id}[/dim]"]
    if event.ref:
        bits.append(f"[bold]{event.ref}[/bold]")
    if detail > DetailLevel.MINIMAL and event.current_stage:
        bits.append(f"[italic]{event.current_stage}[/italic]")
    if event.jobs_total is None:
        bits.append("[dim]loading jobs…[/dim]")
    else:
        counts = f"{event.jobs_done}/{event.jobs_total} jobs"
        if event.failed_jobs:
            if detail == DetailLevel.FULL:
                counts += f", failed: {', '.join(event.failed_jobs)}"
            else:
                counts += f", {len(event.failed_jobs)} failed"
        if retries:
            counts += f", {retries} retried"
        bits.append(f"[dim]{counts}[/dim]")
    if event.pipeline_elapsed is not None:
        bits.append(f"[dim]{format_duration(event.pipeline_elapsed)} elapsed[/dim]")
    if detail > DetailLevel.MINIMAL and event.eta_seconds is not None:
        bits.append(f"[dim italic]~{format_duration(event.eta_seconds)} left[/dim italic]")
    return "  " + " · ".join(bits)


def _final_renderable(event: ResultEvent) -> RenderableType:
    if event.reason == "timeout":
        if event.pipeline_id is None:
            return Text.from_markup("[yellow]⏱[/yellow]  timed out waiting for a pipeline to appear")
        text = f"[yellow]⏱[/yellow]  #{event.pipeline_id} timed out waiting (status: {event.status})"
        return Text.from_markup(text)

    is_success = event.status == "success"
    symbol = "✓" if is_success else "✖"
    style = "bold green" if is_success else "bold red"
    line = (
        f"{symbol}  [dim]#{event.pipeline_id}[/dim] [bold]{event.ref}[/bold] · "
        f"[{style}]{event.status}[/{style}] · {event.jobs_done}/{event.jobs_total} jobs"
    )
    if event.failed_jobs:
        line += f" · {len(event.failed_jobs)} failed"
    if event.retries:
        line += f" · {event.retries} retried"
    if event.duration is not None:
        line += f" · {format_duration(event.duration)}"

    renderables: list[RenderableType] = [Text.from_markup(line)]
    if event.failed_jobs:
        renderables.append(Text.from_markup(f"   failed: {', '.join(event.failed_jobs)}"))
    return Group(*renderables)


async def render_live(events: AsyncIterator[AttachEvent], detail: DetailLevel = DetailLevel.NORMAL) -> AttachEvent:
    """Consume attach()'s event stream as a redrawing single-line TTY view.

    A "switched" event (--follow rebind) is additionally printed as a
    one-off line above the live region, at every --detail level including
    "none" — a human watching the live view should never be left wondering
    why the pipeline id silently changed, even if they've asked for minimal
    ongoing content. A "retry" event is printed the same way, for the same
    reason — otherwise a job would appear to restart itself — but honours
    --detail, so "none" stays silent until the final state.

    Returns the final `result` event so the caller can map it to an exit
    code — attach() always ends with one.
    """
    result: AttachEvent | None = None
    # Only the final event carries a retry count, so the live line's count
    # is accumulated here as retries arrive.
    retries = 0
    with Live(console=console, refresh_per_second=8) as live:
        async for event in events:
            if isinstance(event, SwitchedEvent):
                live.console.print(f"[yellow]⚠[/yellow]  {event.message}")
            if isinstance(event, RetryEvent):
                retries += 1
                if _visible_at(event, detail):
                    live.console.print(
                        f"[cyan]↻[/cyan]  retrying {event.job_name} "
                        f"(#{event.new_job_id}, attempt {event.attempt})"
                    )
            if isinstance(event, ResultEvent):
                result = event
                live.update(_final_renderable(event))
                break
            live.update(
                Spinner("dots", text=Text.from_markup(_live_markup(event, detail, retries=retries)))
            )
    assert result is not None, "attach() event stream ended without a result event"
    return result
