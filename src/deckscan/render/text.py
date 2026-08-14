"""Text measurement and fitting helpers shared by both renderers.

Type sizes are fixed by the theme and never shrunk to make content fit — pages
are fitted by dropping the lowest-ranked items, not by shrinking type.
"""

from __future__ import annotations

from reportlab.pdfbase.pdfmetrics import stringWidth

ELLIPSIS = "…"


def wrap(text: str, font: str, size: float, max_width: float) -> list[str]:
    """Greedy word wrap that also hard-breaks words wider than the column."""
    lines: list[str] = []
    for paragraph in text.replace("\r", "").split("\n"):
        words = paragraph.split()
        if not words:
            continue
        current = ""
        for word in words:
            for piece in _split_long_word(word, font, size, max_width):
                trial = f"{current} {piece}".strip()
                if not current or stringWidth(trial, font, size) <= max_width:
                    current = trial
                else:
                    lines.append(current)
                    current = piece
        if current:
            lines.append(current)
    return lines


def _split_long_word(word: str, font: str, size: float, max_width: float) -> list[str]:
    if stringWidth(word, font, size) <= max_width:
        return [word]
    pieces: list[str] = []
    current = ""
    for char in word:
        if current and stringWidth(current + char, font, size) > max_width:
            pieces.append(current)
            current = char
        else:
            current += char
    if current:
        pieces.append(current)
    return pieces


def fit_line(text: str, font: str, size: float, max_width: float) -> str:
    """Single line, truncated with an ellipsis when it does not fit."""
    text = " ".join(text.split())
    if stringWidth(text, font, size) <= max_width:
        return text
    return ellipsize(text, font, size, max_width)


def ellipsize(text: str, font: str, size: float, max_width: float) -> str:
    text = text.rstrip()
    if text.endswith(ELLIPSIS):
        return text
    while text and stringWidth(text + ELLIPSIS, font, size) > max_width:
        text = text[:-1].rstrip()
    return (text + ELLIPSIS) if text else ELLIPSIS
