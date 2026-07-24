from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import msgspec
from rich.console import Group, RenderableType
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from ddgl.model.attach import AttachEvent, AttachEventKind
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


def event_to_text(event: AttachEvent, detail: str = "normal") -> str:
    """Render a single AttachEvent as one human-readable, agent-greppable line.

    Does not decide whether the event should be shown at all — that's a
    --detail *filtering* concern (see _visible_at / render_lines). This only
    formats a given event once the caller has decided to show it.
    """
    ts = _hhmmss(event.ts)
    match event.kind:
        case AttachEventKind.SNAPSHOT:
            ref = f" {event.ref}" if event.ref else ""
            # jobs_total is None on attach()'s first ("attached, jobs not
            # yet loaded") snapshot — see AttachEvent's docstring. Must not
            # render as the literal string "None jobs".
            jobs_part = "loading jobs…" if event.jobs_total is None else f"{event.jobs_total} jobs"
            return f"[{ts}]{_tag('INFO')}attach #{event.pipeline_id}{ref} — {event.status}, {jobs_part}"
        case AttachEventKind.JOB:
            old = event.old_status or "new"
            line = f"[{ts}]{_tag('JOB')}{event.job_name} {old}→{event.status}"
            if event.duration is not None:
                line += f" ({format_duration(event.duration)})"
            if detail == "full" and event.message:
                line += f" — {event.message}"
            return line
        case AttachEventKind.PIPELINE:
            return f"[{ts}]{_tag('PIPE')}{event.old_status}→{event.status}"
        case AttachEventKind.POLL:
            return f"[{ts}]{_tag('POLL')}{_poll_summary(event)}"
        case AttachEventKind.HEARTBEAT:
            return f"[{ts}]{_tag('BEAT')}{event.jobs_done}/{event.jobs_total} jobs, {len(event.failed_jobs)} failed"
        case AttachEventKind.SWITCHED:
            return f"[{ts}]{_tag('WARN')}{event.message}"
        case _:
            return f"[{ts}]{_tag('FINAL')}{_final_text(event)}"


_SUMMARY_KINDS = frozenset(
    {AttachEventKind.SNAPSHOT, AttachEventKind.POLL, AttachEventKind.HEARTBEAT}
)
_TERMINAL_STATUSES = frozenset({"success", "failed", "canceled", "skipped"})


def _visible_at(event: AttachEvent, detail: str) -> bool:
    """Whether `event` should be printed at the given --detail level.

    --detail controls which *already-emitted* events get shown — it never
    changes what the engine emits (see core/attach.py).

    none:    only the final result — nothing else, ever.
    minimal: only summary-shaped lines (snapshot/heartbeat) + result.
    normal:  summaries + pipeline transitions + switched (both rare/
             low-noise, shown unconditionally) + job transitions, but only
             the ones reaching a TERMINAL status — job transitions are
             where nearly all the noise lives on a large pipeline (hundreds
             of created→running/running→pending blips), so that's the one
             kind gated by status at this level.
    full:    everything, unfiltered (current full behavior).

    "result" always shows at every level: it's the one guaranteed
    self-sufficient line every output mode promises (see the design doc).
    """
    if event.kind == AttachEventKind.RESULT:
        return True
    if detail == "none":
        return False
    if event.kind in _SUMMARY_KINDS:
        return True
    if detail == "minimal":
        return False
    if detail == "full":
        return True
    if event.kind in (AttachEventKind.PIPELINE, AttachEventKind.SWITCHED):
        return True
    return event.status in _TERMINAL_STATUSES  # "job": terminal-only at normal


async def render_lines(
    events: AsyncIterator[AttachEvent], *, as_json: bool = False, detail: str = "normal"
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
        if event.kind == AttachEventKind.RESULT:
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


def _live_markup(event: AttachEvent, detail: str) -> str:
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
    if detail == "none":
        return ""

    bits = [f"[dim]#{event.pipeline_id}[/dim]"]
    if event.ref:
        bits.append(f"[bold]{event.ref}[/bold]")
    if detail != "minimal" and event.current_stage:
        bits.append(f"[italic]{event.current_stage}[/italic]")
    if event.jobs_total is None:
        bits.append("[dim]loading jobs…[/dim]")
    else:
        counts = f"{event.jobs_done}/{event.jobs_total} jobs"
        if event.failed_jobs:
            if detail == "full":
                counts += f", failed: {', '.join(event.failed_jobs)}"
            else:
                counts += f", {len(event.failed_jobs)} failed"
        bits.append(f"[dim]{counts}[/dim]")
    if event.pipeline_elapsed is not None:
        bits.append(f"[dim]{format_duration(event.pipeline_elapsed)} elapsed[/dim]")
    if detail != "minimal" and event.eta_seconds is not None:
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


async def render_live(events: AsyncIterator[AttachEvent], detail: str = "normal") -> AttachEvent:
    """Consume attach()'s event stream as a redrawing single-line TTY view.

    A "switched" event (--follow rebind) is additionally printed as a
    one-off line above the live region, at every --detail level including
    "none" — a human watching the live view should never be left wondering
    why the pipeline id silently changed, even if they've asked for minimal
    ongoing content.

    Returns the final `result` event so the caller can map it to an exit
    code — attach() always ends with one.
    """
    result: AttachEvent | None = None
    with Live(console=console, refresh_per_second=8) as live:
        async for event in events:
            if event.kind == AttachEventKind.SWITCHED:
                live.console.print(f"[yellow]⚠[/yellow]  {event.message}")
            if event.kind == AttachEventKind.RESULT:
                result = event
                live.update(_final_renderable(event))
                break
            live.update(Spinner("dots", text=Text.from_markup(_live_markup(event, detail))))
    assert result is not None, "attach() event stream ended without a result event"
    return result
