# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

import os
import pydoc

from rich.console import Console
from rich.pager import Pager

console = Console()
err_console = Console(stderr=True)


class EnvPager(Pager):
    """Pipe content to `$PAGER` (default `less`), ignoring `$MANPAGER`."""

    # Rich's default pager defers to pydoc, which prefers $MANPAGER: that is often a man-specific
    # filter (e.g. `col -b`) that strips the escape bytes and leaves garbage like `1;36m...0m`.
    def show(self, content: str) -> None:
        # Same default as git: keep colors (R), quit if one screen (F), don't clear on exit (X).
        os.environ.setdefault("LESS", "FRX")
        pydoc.pipepager(content, os.environ.get("PAGER") or "less")
