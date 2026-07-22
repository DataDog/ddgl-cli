from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import msgspec
from rich.console import Group, RenderableType
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from ddgl.model.attach import AttachEvent
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


def _final_text(event: AttachEvent) -> str:
    if event.reason == "timeout":
        if event.pipeline_id is None:
            return "Timed out waiting for a pipeline to appear."
        return f"Timed out waiting for pipeline #{event.pipeline_id} (status: {event.status})."
    status = event.status.upper() if event.status else "UNKNOWN"
    text = f"Pipeline #{event.pipeline_id} {status}."
    if event.failed_jobs:
        text += f" Failed jobs: {', '.join(event.failed_jobs)}"
    return text


def event_to_text(event: AttachEvent, detail: str = "normal") -> str:
    """Render a single AttachEvent as one human-readable, agent-greppable line.

    Does not decide whether the event should be shown at all — that's a
    --detail *filtering* concern (see _DETAIL_KINDS / render_lines). This
    only formats a given event once the caller has decided to show it.
    """
    ts = _hhmmss(event.ts)
    if event.kind == "snapshot":
        ref = f" {event.ref}" if event.ref else ""
        # jobs_total is None on attach()'s first ("attached, jobs not yet
        # loaded") snapshot — see AttachEvent's docstring. Must not render
        # as the literal string "None jobs".
        jobs_part = "loading jobs…" if event.jobs_total is None else f"{event.jobs_total} jobs"
        return f"[{ts}]{_tag('INFO')}attach #{event.pipeline_id}{ref} — {event.status}, {jobs_part}"
    if event.kind == "job":
        old = event.old_status or "new"
        line = f"[{ts}]{_tag('JOB')}{event.job_name} {old}→{event.status}"
        if event.duration is not None:
            line += f" ({format_duration(event.duration)})"
        if detail == "full" and event.message:
            line += f" — {event.message}"
        return line
    if event.kind == "pipeline":
        return f"[{ts}]{_tag('PIPE')}{event.old_status}→{event.status}"
    if event.kind == "heartbeat":
        return f"[{ts}]{_tag('BEAT')}{event.jobs_done}/{event.jobs_total} jobs, {len(event.failed_jobs)} failed"
    if event.kind == "switched":
        return f"[{ts}]{_tag('WARN')}{event.message}"
    return f"[{ts}]{_tag('FINAL')}{_final_text(event)}"


# --detail controls which *already-emitted* event kinds get shown — it never
# changes what the engine emits (see core/attach.py). "result" always shows:
# it's the self-sufficient final line every mode guarantees (see design doc).
_DETAIL_KINDS: dict[str, frozenset[str]] = {
    "none": frozenset({"pipeline", "switched", "result"}),
    "minimal": frozenset({"snapshot", "pipeline", "switched", "result"}),
    "normal": frozenset({"snapshot", "pipeline", "job", "switched", "heartbeat", "result"}),
    "full": frozenset({"snapshot", "pipeline", "job", "switched", "heartbeat", "result"}),
}


async def render_lines(
    events: AsyncIterator[AttachEvent], *, as_json: bool = False, detail: str = "normal"
) -> AttachEvent:
    """Consume attach()'s event stream, printing one line per event.

    as_json=True switches every line to a serialized AttachEvent (JSONL) —
    used for --json, forced even when stdout is a TTY. Otherwise each event
    is filtered by --detail and rendered via event_to_text. Prints with
    markup/highlighting disabled (this output must never have its literal
    "[TAG]" brackets misinterpreted as Rich style markup) and soft_wrap
    enabled (a long line — e.g. --json's JSONL, or a long job name — must
    stay exactly one physical line; Rich word-wraps to terminal width by
    default, which would silently split one event across multiple lines).

    Returns the final `result` event so the caller can map it to an exit
    code — attach() always ends with one.
    """
    allowed = _DETAIL_KINDS.get(detail, _DETAIL_KINDS["normal"])
    result: AttachEvent | None = None
    async for event in events:
        if event.kind == "result":
            result = event
        if as_json:
            console.print(
                msgspec.json.encode(event).decode(), highlight=False, markup=False, soft_wrap=True
            )
            continue
        if event.kind not in allowed:
            continue
        console.print(event_to_text(event, detail), highlight=False, markup=False, soft_wrap=True)
    assert result is not None, "attach() event stream ended without a result event"
    return result


# ---------------------------------------------------------------------------
# Live mode (human · TTY, or --live)
# ---------------------------------------------------------------------------


def _live_markup(event: AttachEvent) -> str:
    """Build the redrawing single-line status. Every non-result event
    carries the same rollup fields (see AttachEvent's docstring), so this
    doesn't need to branch on `.kind` — except jobs_total, which is None on
    attach()'s first ("attached, jobs not yet loaded") snapshot."""
    bits = [f"[dim]#{event.pipeline_id}[/dim]"]
    if event.ref:
        bits.append(f"[bold]{event.ref}[/bold]")
    if event.current_stage:
        bits.append(f"[italic]{event.current_stage}[/italic]")
    if event.jobs_total is None:
        bits.append("[dim]loading jobs…[/dim]")
    else:
        counts = f"{event.jobs_done}/{event.jobs_total} jobs"
        if event.failed_jobs:
            counts += f", {len(event.failed_jobs)} failed"
        bits.append(f"[dim]{counts}[/dim]")
    if event.pipeline_elapsed is not None:
        bits.append(f"[dim]{format_duration(event.pipeline_elapsed)} elapsed[/dim]")
    if event.eta_seconds is not None:
        bits.append(f"[dim italic]~{format_duration(event.eta_seconds)} left[/dim italic]")
    return "  " + " · ".join(bits)


def _final_renderable(event: AttachEvent) -> RenderableType:
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
    if event.duration is not None:
        line += f" · {format_duration(event.duration)}"

    renderables: list[RenderableType] = [Text.from_markup(line)]
    if event.failed_jobs:
        renderables.append(Text.from_markup(f"   failed: {', '.join(event.failed_jobs)}"))
    return Group(*renderables)


async def render_live(events: AsyncIterator[AttachEvent]) -> AttachEvent:
    """Consume attach()'s event stream as a redrawing single-line TTY view.

    A "switched" event (--follow rebind) is additionally printed as a
    one-off line above the live region — a state change that important
    shouldn't be silently swallowed by the next redraw.

    Returns the final `result` event so the caller can map it to an exit
    code — attach() always ends with one.
    """
    result: AttachEvent | None = None
    with Live(console=console, refresh_per_second=8) as live:
        async for event in events:
            if event.kind == "switched":
                live.console.print(f"[yellow]⚠[/yellow]  {event.message}")
            if event.kind == "result":
                result = event
                live.update(_final_renderable(event))
                break
            live.update(Spinner("dots", text=Text.from_markup(_live_markup(event))))
    assert result is not None, "attach() event stream ended without a result event"
    return result
