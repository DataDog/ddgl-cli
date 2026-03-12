from __future__ import annotations

from rich.text import Text

# Brand colours used across the TUI.
PURPLE = "#774AA4"  # DataDog purple
ORANGE = "#FC6D26"  # GitLab orange


def gradient_text(text: str, start_hex: str = PURPLE, end_hex: str = ORANGE) -> Text:
    """Return a Rich Text with per-character colour gradient between two hex colours."""
    t = Text()
    n = len(text)
    if n == 0:
        return t
    def _parse(h: str) -> tuple[int, int, int]:
        return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)

    sr, sg, sb = _parse(start_hex)
    er, eg, eb = _parse(end_hex)
    for i, ch in enumerate(text):
        ratio = i / max(n - 1, 1)
        r = int(sr + (er - sr) * ratio)
        g = int(sg + (eg - sg) * ratio)
        b = int(sb + (eb - sb) * ratio)
        t.append(ch, style=f"#{r:02X}{g:02X}{b:02X}")
    return t
