"""Colour, font, and spacing tokens, resolved from ``rules.yaml``.

Nothing here computes; it only turns configured strings into reportlab objects
so the renderers never hard-code a colour.
"""

from __future__ import annotations

from dataclasses import dataclass

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import letter

from deckscan.config import Settings
from deckscan.models import Severity

PAGE_WIDTH: float = float(letter[0])
PAGE_HEIGHT: float = float(letter[1])
MARGIN = 36.0
GUTTER = 12.0


@dataclass(frozen=True)
class Theme:
    """Everything the renderers need to draw, and nothing they need to decide."""

    settings: Settings

    @property
    def body_font(self) -> str:
        return self.settings.render.font_family

    @property
    def bold_font(self) -> str:
        family = self.settings.render.font_family
        return f"{family}-Bold" if family == "Helvetica" else family

    @property
    def size_title(self) -> int:
        return self.settings.render.font_size_title

    @property
    def size_section(self) -> int:
        return self.settings.render.font_size_section

    @property
    def size_body(self) -> int:
        return self.settings.render.font_size_body

    @property
    def size_small(self) -> int:
        return self.settings.render.font_size_small

    def severity_color(self, severity: Severity) -> Color:
        return HexColor(self.settings.render.severity_colors[severity])

    @property
    def text(self) -> Color:
        return HexColor(self.settings.render.text_color)

    @property
    def muted(self) -> Color:
        return HexColor(self.settings.render.muted_color)

    @property
    def rule(self) -> Color:
        return HexColor(self.settings.render.rule_color)

    @property
    def background(self) -> Color:
        return HexColor(self.settings.render.background_color)

    def band_color(self, score: int) -> Color:
        """Grounding-band colour: well grounded / needs grounding / weakly grounded."""
        band = self.settings.band_key(score)
        mapping = {
            "well_grounded": "info",
            "needs_grounding": "medium",
            "weakly_grounded": "critical",
        }
        return self.severity_color(mapping[band])  # type: ignore[arg-type]

    @property
    def content_width(self) -> float:
        return PAGE_WIDTH - 2 * MARGIN
