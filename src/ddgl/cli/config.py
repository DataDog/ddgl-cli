# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""``ddgl config`` — show currently resolved config."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec
import rich_click as click

from ddgl.config import get_config_file_path, load_config, load_config_file
from ddgl.render._console import console

if TYPE_CHECKING:
    from ddgl.config import Config
    from ddgl.model import ConfigFile

@click.group()
def config_group() -> None:
    """Query currently resolved config parameters"""
    pass


@click.command()
@click.option(
    "--json", "output_json", is_flag=True, default=False, help="Output as JSON."
)
def path(
    output_json: bool,
) -> None:
    """Shows the path of the currently loaded config file."""
    path = get_config_file_path()
    if output_json:
        console.print_json(data=str(path.resolve()))
        return

    console.print(path, soft_wrap=True)


@click.command()
@click.option(
    "--json", "output_json", is_flag=True, default=False, help="Output as JSON."
)
@click.option(
    "--raw",
    "-r",
    "output_raw",
    is_flag=True,
    default=False,
    help="Show the raw loaded config file instead of the fully-resolved config",
)
@click.option(
    "--censor/--no-censor",
    "censor",
    is_flag=True,
    default=True,
    help="Hide sensitive data from output (tokens...)",
)
def show(output_json: bool, output_raw: bool, censor: bool) -> None:
    """Shows the currently resolved Config object"""
    data: ConfigFile | Config

    if output_raw:
        data = load_config_file()
    else:
        data = asyncio.run(load_config())
        if censor:
            data = msgspec.structs.replace(data, private_token="*" * 10)

    if output_json:
        # Don't use print_json, we don't really care about pretty-printing
        console.print(msgspec.json.encode(data).decode(), soft_wrap=True)
        return

    # Sanitize any Nones before exporting as toml
    sanitized = _strip_none(msgspec.to_builtins(data))
    console.print(msgspec.toml.encode(sanitized).decode(), soft_wrap=True)

def _strip_none(obj):
    if obj is None:
        return "N/A"
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    return obj


config_group.add_command(path)
config_group.add_command(show)
