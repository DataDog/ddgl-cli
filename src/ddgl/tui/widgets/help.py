# Unless explicitly stated otherwise all files in this repository are licensed under the Apache-2.0 License.
# This product includes software developed at Datadog (https://www.datadoghq.com/) Copyright 2026 Datadog, Inc.

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Static

from ddgl.tui.gradient import gradient_text

_HELP_CONTENT = """\
[bold]Navigation[/bold]
  [dim]↑ / ↓  or  k / j[/dim]   Move cursor
  [dim]Space[/dim]               Expand / collapse matrix group
  [dim]Enter[/dim]               Open job detail

[bold]Actions[/bold]
  [dim]r[/dim]        Retry selected job
  [dim]Ctrl+R[/dim]   Refresh pipeline
  [dim]o[/dim]        Open selected job or pipeline in browser
  [dim]p[/dim]        Switch pipeline or ref
  [dim]/[/dim]        Focus search bar
  [dim]Esc[/dim]      Return focus to job list
  [dim]Ctrl+K[/dim]   Clear search
  [dim]s[/dim]        Cycle sort mode  (or use the Sort button)
  [dim]q[/dim]        Quit

[bold]Sort modes[/bold]
  Stage · A–Z · Start time

[bold]Filter syntax[/bold]  (type in the search bar)
  [dim]status:failed[/dim]   filter by status — repeat for OR
  [dim]stage:build[/dim]     filter by stage  — repeat for OR
  Plain text        fuzzy match on job name
  Example: [italic]status:failed status:running stage:deploy auth[/italic]

[bold]Matrix groups[/bold]
  Jobs sharing a base name and stage are collapsed into a group row.
  [dim]Space[/dim] or [dim]Enter[/dim] on a group row to expand / collapse.
  Grouping is disabled when any filter is active.
"""


class HelpModal(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="help"):
            yield Static(_HELP_CONTENT, id="help-content", markup=True)

    def on_mount(self) -> None:
        self.border_title = gradient_text("Help  ·  ?")
