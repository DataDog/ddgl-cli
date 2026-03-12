from __future__ import annotations

import logging
import os

import click

from ddgl.cli.jobs import jobs
from ddgl.cli.logs import logs
from ddgl.cli.pipelines import pipelines

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%H:%M:%S"


def setup_logging(verbosity: int = 0) -> None:
    """Configure the ddgl logger hierarchy.

    Level resolution (first match wins):
        1. verbosity >= 2 -> DEBUG
        2. verbosity == 1 -> INFO
        3. DDGL_LOG_LEVEL env var
        4. Default: WARNING
    """
    logging.addLevelName(logging.DEBUG, "DEBG")
    logging.addLevelName(logging.INFO, "INFO")
    logging.addLevelName(logging.WARNING, "WARN")
    logging.addLevelName(logging.ERROR, "ERRO")
    logging.addLevelName(logging.CRITICAL, "CRIT")

    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    else:
        env_level = os.environ.get("DDGL_LOG_LEVEL", "").upper()
        env_val = getattr(logging, env_level, None) if env_level else None
        level = env_val if isinstance(env_val, int) else logging.WARNING

    ddgl_logger = logging.getLogger("ddgl")
    ddgl_logger.setLevel(level)

    if not ddgl_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
        ddgl_logger.addHandler(handler)

    ddgl_logger.propagate = False


@click.group()
@click.version_option(package_name="ddgl")
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v/-vv).")
@click.option("-y", "--yes", is_flag=True, default=False, help="Skip confirmation prompts.")
@click.pass_context
def main(ctx: click.Context, verbose: int, yes: bool) -> None:
    """ddgl — Terminal-based GitLab client."""
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["yes"] = yes


main.add_command(pipelines)
main.add_command(jobs)
main.add_command(logs)
