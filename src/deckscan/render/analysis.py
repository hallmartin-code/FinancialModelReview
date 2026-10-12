"""The financial analysis report: every finding the rule engine produced.

Layout, top to bottom: header band, snapshot tiles, traction chart, red flags,
needs grounding, missing from the model, methodology and sources.

Nothing is dropped to make room. The screen this replaces was one US Letter page
and fitted by *deleting* content - lowest-ranked flags first, then grounding
lines, then the chart - so a model with eleven findings reported eight and said
so in small print. Here the page breaks instead and the report continues, which
is what lets each flag carry the whole of what the rule engine knows about it:
the finding with its figures, why it matters, the question to put to the
founder, and the cells the figures were read from.

Type sizes are fixed; nothing is ever shrunk to fit.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

from deckscan.analyze.scoring import GroundingItem, grounding_items
from deckscan.config import Settings
from deckscan.formatting import fmt_metric
from deckscan.models import SEVERITY_RANK, DeckAnalysis, Flag
from deckscan.render.charts import revenue_chart_png
from deckscan.render.text import fit_line, wrap
from deckscan.render.theme import GUTTER, MARGIN, PAGE_HEIGHT, PAGE_WIDTH, Theme

HEADER_HEIGHT = 62.0
FOOTER_HEIGHT = 26.0
TILE_HEIGHT = 30.0
TILE_GAP = 6.0
SECTION_GAP = 14.0
SECTION_LABEL_GAP = 7.0
FLAG_GAP = 9.0
LINE_GAP = 1.6
CHART_HEIGHT = 150.0
CHIP_WIDTH = 40.0
#: Flag body text hangs to the right of the severity chip.
BODY_INDENT = CHIP_WIDTH + 6

#: Snapshot tiles, in page order: canonical metric name -> tile label.
TILES: tuple[tuple[str, str], ...] = (
    ("arr", "ARR"),
    ("revenue", "Revenue"),
    ("gross_margin", "Gross margin"),
    ("burn_monthly", "Monthly burn"),
    ("runway_months", "Runway"),
    ("ltv_cac_ratio", "LTV:CAC"),
)


def render_analysis(
    analysis: DeckAnalysis,
    output_path: Path,
    settings: Settings,
    sources: list[str] | None = None,
) -> Path:
    """Render the financial analysis report for ``analysis`` at ``output_path``."""
    theme = Theme(settings)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    canvas = pdfcanvas.Canvas(str(output_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    canvas.setTitle(f"{analysis.company or 'Company'} - Financial Analysis")
    canvas.setAuthor("deckscan")

    report = _Report(canvas, theme, settings, analysis, sources or [])
    report.draw()
    return output_path


def _chart_points(analysis: DeckAnalysis) -> list[tuple[str, Decimal, bool]]:
    for name in ("revenue", "arr", "mrr"):
        points = analysis.series.get(name)
        if points and len(points) >= 2:
            return [(label, value, label in analysis.projected_periods) for label, value in points]
    return []


def _grounding_text(item: GroundingItem) -> str:
    return f"{item.claim}  ->  {item.substantiation}"


class _Report:
    """A flowing, paginated document. Every ``draw`` goes through :meth:`_room`."""

    def __init__(
        self,
        canvas: pdfcanvas.Canvas,
        theme: Theme,
        settings: Settings,
        analysis: DeckAnalysis,
        sources: list[str],
    ) -> None:
        self.canvas = canvas
        self.theme = theme
        self.settings = settings
        self.analysis = analysis
        self.sources = sources
        self.page = 0
        self.y = 0.0
        self._stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._open_page()

    # --- pagination ----------------------------------------------------------

    @property
    def _floor(self) -> float:
        return MARGIN + FOOTER_HEIGHT

    def _open_page(self) -> None:
        self.page += 1
        self.canvas.setFillColor(self.theme.background)
        self.canvas.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)
        self.y = PAGE_HEIGHT - MARGIN

    def _room(self, height: float) -> None:
        """Start a new page when ``height`` will not fit above the footer."""
        if self.y - height < self._floor:
            self._close_page()
            self._open_page()

    def _close_page(self) -> None:
        theme = self.theme
        canvas = self.canvas
        base = MARGIN + FOOTER_HEIGHT

        canvas.setStrokeColor(theme.rule)
        canvas.setLineWidth(0.4)
        canvas.line(MARGIN, base, PAGE_WIDTH - MARGIN, base)

        canvas.setFillColor(theme.muted)
        canvas.setFont(theme.body_font, theme.size_small)
        label = f"Page {self.page}"
        label_width = canvas.stringWidth(label, theme.body_font, theme.size_small)
        canvas.drawString(
            MARGIN,
            base - theme.size_small - 3,
            fit_line(
                " ".join(self.settings.render.disclaimer.split()),
                theme.body_font,
                theme.size_small,
                theme.content_width - label_width - GUTTER,
            ),
        )
        canvas.drawRightString(PAGE_WIDTH - MARGIN, base - theme.size_small - 3, label)
        canvas.showPage()

    def draw(self) -> None:
        self._header()
        self._tiles()
        self._chart()
        self._flags()
        self._grounding()
        self._gaps()
        self._methodology()
        self._close_page()
        self.canvas.save()

    # --- primitives ----------------------------------------------------------

    def _line(
        self,
        text: str,
        *,
        font: str,
        size: float,
        color: object,
        indent: float = 0.0,
        gap: float = LINE_GAP,
    ) -> None:
        self._room(size + gap)
        self.y -= size + gap
        self.canvas.setFillColor(color)
        self.canvas.setFont(font, size)
        self.canvas.drawString(MARGIN + indent, self.y, text)

    def _paragraph(
        self,
        text: str,
        *,
        font: str,
        size: float,
        color: object,
        indent: float = 0.0,
    ) -> None:
        for line in wrap(text, font, size, self.theme.content_width - indent):
            self._line(line, font=font, size=size, color=color, indent=indent)

    def _section(self, label: str) -> None:
        theme = self.theme
        # Keep a label with at least one line of its section; a heading alone at
        # the foot of a page reads as an empty section.
        self._room(SECTION_GAP + theme.size_small + 3 + theme.size_body + LINE_GAP)
        self.y -= SECTION_GAP
        self.canvas.setFillColor(theme.muted)
        self.canvas.setFont(theme.bold_font, theme.size_small)
        self.canvas.drawString(MARGIN, self.y - theme.size_small, label.upper())
        self.canvas.setStrokeColor(theme.rule)
        self.canvas.setLineWidth(0.4)
        line_y = self.y - theme.size_small - 3
        self.canvas.line(MARGIN, line_y, PAGE_WIDTH - MARGIN, line_y)
        self.y = line_y - SECTION_LABEL_GAP

    # --- sections ------------------------------------------------------------

    def _header(self) -> None:
        theme, analysis, canvas = self.theme, self.analysis, self.canvas
        badge_width = 92.0
        title_width = theme.content_width - badge_width - GUTTER
        top = self.y

        canvas.setFillColor(theme.text)
        canvas.setFont(theme.bold_font, theme.size_title)
        canvas.drawString(
            MARGIN,
            top - theme.size_title,
            fit_line(
                analysis.company or "Company not identified",
                theme.bold_font,
                theme.size_title,
                title_width,
            ),
        )
        line_y = top - theme.size_title - theme.size_body - 3

        canvas.setFillColor(theme.muted)
        canvas.setFont(theme.body_font, theme.size_body)
        if analysis.tagline:
            canvas.drawString(
                MARGIN,
                line_y,
                fit_line(analysis.tagline, theme.body_font, theme.size_body, title_width),
            )
            line_y -= theme.size_body + 2

        facts = [
            analysis.sector or "sector not stated",
            (analysis.stage_guess or "stage not stated").replace("_", " "),
            f"raising {fmt_metric(analysis.raise_ask.value, analysis.raise_ask.unit)}"
            if analysis.raise_ask and analysis.raise_ask.value is not None
            else "raise not stated",
        ]
        canvas.setFont(theme.body_font, theme.size_small)
        canvas.drawString(
            MARGIN,
            line_y,
            fit_line(" · ".join(facts), theme.body_font, theme.size_small, title_width),
        )

        # Grounding badge, right-aligned. Colour AND word, so it survives grayscale.
        band = self.settings.band_label(analysis.grounding_score)
        badge_x = PAGE_WIDTH - MARGIN - badge_width
        canvas.setFillColor(theme.band_color(analysis.grounding_score))
        canvas.rect(badge_x, top - 34, badge_width, 34, stroke=0, fill=1)
        canvas.setFillColor(theme.background)
        canvas.setFont(theme.bold_font, 15)
        canvas.drawCentredString(
            badge_x + badge_width / 2, top - 17, f"{analysis.grounding_score}/100"
        )
        canvas.setFont(theme.body_font, theme.size_small)
        canvas.drawCentredString(badge_x + badge_width / 2, top - 29, band)

        self.y = top - HEADER_HEIGHT

    def _tiles(self) -> None:
        theme, analysis, canvas = self.theme, self.analysis, self.canvas
        self._section("Snapshot")
        self._room(TILE_HEIGHT)

        tiles = TILES[: self.settings.render.max_metric_tiles]
        count = len(tiles)
        width = (theme.content_width - TILE_GAP * (count - 1)) / count
        top = self.y
        x = MARGIN
        for name, label in tiles:
            metric = analysis.primary(name)
            value = fmt_metric(metric.value, metric.unit) if metric else ""
            canvas.setLineWidth(0.5)
            canvas.setStrokeColor(theme.rule)
            if not value:
                canvas.setDash(2, 2)
            canvas.rect(x, top - TILE_HEIGHT, width, TILE_HEIGHT, stroke=1, fill=0)
            canvas.setDash()

            canvas.setFillColor(theme.muted)
            canvas.setFont(theme.body_font, theme.size_small)
            canvas.drawString(
                x + 5, top - 11, fit_line(label, theme.body_font, theme.size_small, width - 10)
            )

            canvas.setFillColor(theme.text if value else theme.muted)
            canvas.setFont(theme.bold_font if value else theme.body_font, theme.size_section)
            shown = value or self.settings.render.not_disclosed_label
            canvas.drawString(
                x + 5, top - 24, fit_line(shown, theme.bold_font, theme.size_section, width - 10)
            )
            x += width + TILE_GAP

        self.y = top - TILE_HEIGHT

    def _chart(self) -> None:
        points = _chart_points(self.analysis)
        render = self.settings.render
        if len(points) < render.min_chart_periods:
            return
        png = revenue_chart_png(
            points,
            actual_color=render.severity_colors["info"],
            projected_color=render.rule_color,
            text_color=render.muted_color,
        )
        if png is None:  # charts.py declines fewer than two points
            return
        self._section("Traction — solid = actual, hatched = projected")

        image = ImageReader(BytesIO(png))
        raw_width, raw_height = image.getSize()
        width, height = float(raw_width), float(raw_height)
        scale = min(self.theme.content_width / width, CHART_HEIGHT / height)
        drawn_height = height * scale
        self._room(drawn_height)
        self.canvas.drawImage(
            image,
            MARGIN,
            self.y - drawn_height,
            width=width * scale,
            height=drawn_height,
            mask="auto",
        )
        self.y -= drawn_height

    def _flags(self) -> None:
        theme, analysis = self.theme, self.analysis
        flags = sorted(analysis.flags, key=lambda f: (SEVERITY_RANK[f.severity], f.code))
        self._section(f"Red flags ({len(flags)})" if flags else "Red flags")

        if not flags:
            # "No red flags" and "nothing could be read" must never look the same:
            # one is a result, the other is the absence of one.
            unread = analysis.nothing_extracted()
            message = (
                self.settings.render.unread_label if unread else self.settings.render.no_flags_label
            )
            self._paragraph(
                " ".join(message.split()),
                font=theme.body_font,
                size=theme.size_body,
                color=theme.severity_color("critical") if unread else theme.muted,
            )
            return

        for flag in flags:
            self._flag(flag)

    def _flag(self, flag: Flag) -> None:
        theme, canvas = self.theme, self.canvas
        # Keep the chip and heading with the first line of the finding.
        self._room(theme.size_section + 1.5 + 2 * (theme.size_body + LINE_GAP))

        self.y -= theme.size_section + 1.5
        colour = theme.severity_color(flag.severity)
        # Colour is never the only signal: the severity word rides in the chip.
        canvas.setFillColor(colour)
        canvas.rect(MARGIN, self.y, CHIP_WIDTH, theme.size_section + 1, stroke=0, fill=1)
        canvas.setFillColor(theme.background)
        canvas.setFont(theme.bold_font, theme.size_small - 0.5)
        canvas.drawCentredString(MARGIN + CHIP_WIDTH / 2, self.y + 2.5, flag.severity.upper())

        canvas.setFillColor(theme.text)
        canvas.setFont(theme.bold_font, theme.size_section)
        canvas.drawString(
            MARGIN + BODY_INDENT,
            self.y + 1,
            fit_line(
                flag.title,
                theme.bold_font,
                theme.size_section,
                theme.content_width - BODY_INDENT,
            ),
        )

        self._paragraph(
            flag.finding,
            font=theme.body_font,
            size=theme.size_body,
            color=theme.text,
            indent=BODY_INDENT,
        )
        if flag.why_it_matters:
            self._paragraph(
                f"Why it matters: {flag.why_it_matters}",
                font=theme.body_font,
                size=theme.size_body,
                color=theme.muted,
                indent=BODY_INDENT,
            )
        if flag.ask:
            self._paragraph(
                f"Ask: {flag.ask}",
                font=theme.body_font,
                size=theme.size_body,
                color=theme.muted,
                indent=BODY_INDENT,
            )
        locators = [item.locator for item in flag.evidence if item.locator]
        if locators:
            self._paragraph(
                "Read from: " + ", ".join(dict.fromkeys(locators)),
                font=theme.body_font,
                size=theme.size_small,
                color=theme.muted,
                indent=BODY_INDENT,
            )
        self.y -= FLAG_GAP

    def _grounding(self) -> None:
        theme = self.theme
        items = grounding_items(self.analysis, self.settings)
        self._section(f"Needs grounding ({len(items)})" if items else "Needs grounding")

        if not items:
            self._paragraph(
                "Nothing outstanding.",
                font=theme.body_font,
                size=theme.size_body,
                color=theme.muted,
            )
            return

        for item in items:
            lines = wrap(
                _grounding_text(item), theme.body_font, theme.size_body, theme.content_width - 10
            )
            self._room(theme.size_body + LINE_GAP)
            # The bullet is drawn against the first line's baseline, so place it
            # only once the page it belongs on is settled.
            first = True
            for line in lines:
                self._line(
                    line,
                    font=theme.body_font,
                    size=theme.size_body,
                    color=theme.text,
                    indent=10,
                )
                if first:
                    self.canvas.setFillColor(theme.severity_color(item.severity))
                    self.canvas.circle(MARGIN + 2, self.y + 2.5, 1.8, stroke=0, fill=1)
                    first = False
            self.y -= 2.0

    def _gaps(self) -> None:
        theme, analysis = self.theme, self.analysis
        gaps = analysis.gaps
        self._section(f"Missing from the model ({len(gaps)})" if gaps else "Missing from the model")

        if not gaps:
            self._paragraph(
                "Nothing material missing.",
                font=theme.body_font,
                size=theme.size_body,
                color=theme.muted,
            )
            return

        # The cash-flow gap always reads first when present.
        ordered = sorted(
            gaps, key=lambda gap: (gap.field != "cash_flow", SEVERITY_RANK[gap.severity])
        )
        for gap in ordered:
            self._paragraph(
                f"• {gap.label}",
                font=theme.body_font,
                size=theme.size_body,
                color=theme.text,
                indent=4,
            )

    def _methodology(self) -> None:
        theme, analysis = self.theme, self.analysis
        self._section("Methodology and sources")

        named = ", ".join(self.sources) if self.sources else "none"
        self._paragraph(
            f"Sources: {named}  ·  Run {self._stamp}",
            font=theme.body_font,
            size=theme.size_body,
            color=theme.text,
        )
        for note in analysis.methodology:
            self._paragraph(
                f"• {note}",
                font=theme.body_font,
                size=theme.size_small,
                color=theme.muted,
                indent=4,
            )
