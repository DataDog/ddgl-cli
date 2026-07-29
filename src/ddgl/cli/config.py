# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

"""``ddgl config`` — show currently resolved config."""

from __future__ import annotations

import rich_click as click

from ddgl.config import get_config_file_path
from ddgl.render._console import console


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

    console.print(path)

config_group.add_command(path)
