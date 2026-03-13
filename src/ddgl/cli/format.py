"""``ddgl format`` — parse and render GitLab CI traces."""

from __future__ import annotations

import sys
from contextlib import nullcontext

import rich_click as click

from ddgl.format import TraceOptions, format_trace, to_json
from ddgl.format._options import trace_format_options
from ddgl.render._console import console


@click.command()
@trace_format_options
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON.")
@click.option("--no-pager", is_flag=True, default=False, help="Disable the pager.")
@click.argument("source", default="-", type=click.File("r"))
def format_cmd(
    source: click.utils.LazyFile,
    raw: bool,
    sections: bool,
    strip: bool,
    timestamps: bool,
    highlight: bool,
    color: bool,
    output_json: bool,
    no_pager: bool,
) -> None:
    """Parse and render a GitLab CI job trace.

    Reads from SOURCE (file path or `-` for stdin, default stdin).
    """
    text = source.read()

    if output_json:
        click.echo(to_json(text))
        return

    if raw:
        options = TraceOptions.raw()
    else:
        options = TraceOptions(
            sections=sections,
            strip=strip,
            timestamps=timestamps,
            highlight=highlight,
            color=color and sys.stdout.isatty(),
        )

    renderables = format_trace(text, options)

    use_pager = not no_pager and console.is_terminal
    with console.pager(styles=True) if use_pager else nullcontext():
        for item in renderables:
            console.print(item, highlight=False)
