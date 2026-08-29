"""The financial red-flag screen: one US Letter page, never two.

Layout, top to bottom: header band, snapshot tiles, traction chart, red flags,
needs grounding, missing from the model, footer.

Overflow is handled by measuring the whole page before drawing anything and
reducing *item counts* — lowest-ranked flags first, then grounding lines, then
the chart. Type sizes are fixed; nothing is ever shrunk to fit.
"""

from __future__ import annotations

from dataclasses import dataclass
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
FOOTER_HEIGHT = 34.0
TILE_HEIGHT = 30.0
TILE_GAP = 6.0
SECTION_GAP = 11.0
SECTION_LABEL_GAP = 7.0
FLAG_GAP = 5.5
LINE_GAP = 1.6
CHART_HEIGHT = 74.0
CHIP_WIDTH = 40.0

#: Snapshot tiles, in page order: canonical metric name -> tile label.
TILES: tuple[tuple[str, str], ...] = (
    ("arr", "ARR"),
    ("revenue", "Revenue"),
    ("gross_margin", "Gross margin"),
    ("burn_monthly", "Monthly burn"),
    ("runway_months", "Runway"),
    ("ltv_cac_ratio", "LTV:CAC"),
)


@dataclass
class _Plan:
    """What will be drawn, after fitting."""

    flags: list[Flag]
    grounding: list[GroundingItem]
    chart: bytes | None
    dropped_flags: int


def render_onepager(
    analysis: DeckAnalysis,
    output_path: Path,
    settings: Settings,
    sources: list[str] | None = None,
) -> Path:
    """Render the financial screen for ``analysis`` at ``output_path``."""
    theme = Theme(settings)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    canvas = pdfcanvas.Canvas(str(output_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    canvas.setTitle(f"{analysis.company or 'Company'} - Financial Screen")
    canvas.setAuthor("deckscan")

    plan = _fit(analysis, settings, theme)

    canvas.setFillColor(theme.background)
    canvas.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)

    y = PAGE_HEIGHT - MARGIN
    y = _draw_header(canvas, theme, analysis, y)
    y = _draw_tiles(canvas, theme, analysis, y - SECTION_GAP)
    if plan.chart is not None:
        y = _draw_chart(canvas, theme, plan.chart, y - SECTION_GAP)
    y = _draw_flags(canvas, theme, settings, analysis, plan, y - SECTION_GAP)
    y = _draw_grounding(canvas, theme, plan.grounding, y - SECTION_GAP)
    _draw_gaps(canvas, theme, analysis, y - SECTION_GAP)
    _draw_footer(canvas, theme, settings, analysis, sources or [])

    canvas.showPage()
    canvas.save()
    return output_path


# --- fitting -----------------------------------------------------------------


def _chart_points(analysis: DeckAnalysis) -> list[tuple[str, Decimal, bool]]:
    for name in ("revenue", "arr", "mrr"):
        points = analysis.series.get(name)
        if points and len(points) >= 2:
            return [(label, value, label in analysis.projected_periods) for label, value in points]
    return []


def _fit(analysis: DeckAnalysis, settings: Settings, theme: Theme) -> _Plan:
    """Reduce item counts until the page fits. Never changes a font size."""
    render = settings.render
    all_flags = analysis.flags[: render.max_flags]
    dropped = max(0, len(analysis.flags) - len(all_flags))
    grounding = grounding_items(analysis, settings)

    points = _chart_points(analysis)
    chart: bytes | None = None
    if len(points) >= render.min_chart_periods:
        chart = revenue_chart_png(
            points,
            actual_color=render.severity_colors["info"],
            projected_color=render.rule_color,
            text_color=render.muted_color,
        )

    available = PAGE_HEIGHT - MARGIN - FOOTER_HEIGHT - MARGIN
    flags = list(all_flags)
    while _height(theme, settings, analysis, flags, grounding, chart) > available:
        if len(flags) > 1:
            flags.pop()
            dropped += 1
            continue
        if len(grounding) > 1:
            grounding.pop()
            continue
        if chart is not None:
            chart = None
            continue
        break

    return _Plan(flags=flags, grounding=grounding, chart=chart, dropped_flags=dropped)


def _height(
    theme: Theme,
    settings: Settings,
    analysis: DeckAnalysis,
    flags: list[Flag],
    grounding: list[GroundingItem],
    chart: bytes | None,
) -> float:
    total = HEADER_HEIGHT + SECTION_GAP + TILE_HEIGHT
    if chart is not None:
        total += SECTION_GAP + CHART_HEIGHT
    total += SECTION_GAP + SECTION_LABEL_GAP + _flags_height(theme, flags, settings)
    total += SECTION_GAP + SECTION_LABEL_GAP + _grounding_height(theme, grounding)
    total += SECTION_GAP + SECTION_LABEL_GAP + _gaps_height(theme, analysis)
    return total


def _flag_lines(theme: Theme, flag: Flag) -> list[str]:
    width = theme.content_width - CHIP_WIDTH - 6
    locator = f"  [{flag.first_locator}]" if flag.first_locator else ""
    return wrap(flag.finding + locator, theme.body_font, theme.size_body, width)


def _flags_height(theme: Theme, flags: list[Flag], settings: Settings) -> float:
    if not flags:
        return theme.size_body + LINE_GAP
    total = 0.0
    for flag in flags:
        lines = _flag_lines(theme, flag)
        total += theme.size_section + 1.5
        total += len(lines) * (theme.size_body + LINE_GAP)
        total += FLAG_GAP
    return total + theme.size_small


def _grounding_height(theme: Theme, items: list[GroundingItem]) -> float:
    if not items:
        return theme.size_body + LINE_GAP
    total = 0.0
    for item in items:
        lines = wrap(_grounding_text(item), theme.body_font, theme.size_body, theme.content_width)
        total += len(lines) * (theme.size_body + LINE_GAP) + 2.0
    return total


def _grounding_text(item: GroundingItem) -> str:
    return f"{item.claim}  →  {item.substantiation}"


def _gaps_height(theme: Theme, analysis: DeckAnalysis) -> float:
    text = _gaps_text(analysis)
    lines = wrap(text, theme.body_font, theme.size_body, theme.content_width)
    return max(1, len(lines)) * (theme.size_body + LINE_GAP)


def _gaps_text(analysis: DeckAnalysis) -> str:
    if not analysis.gaps:
        return "Nothing material missing."
    labels = [gap.label for gap in analysis.gaps]
    # The cash-flow gap always reads first when present.
    for index, gap in enumerate(analysis.gaps):
        if gap.field == "cash_flow":
            labels.insert(0, labels.pop(index))
            break
    return ", ".join(labels) + "."


# --- drawing -----------------------------------------------------------------


def _section_label(canvas: pdfcanvas.Canvas, theme: Theme, text: str, y: float) -> float:
    canvas.setFillColor(theme.muted)
    canvas.setFont(theme.bold_font, theme.size_small)
    canvas.drawString(MARGIN, y - theme.size_small, text.upper())
    canvas.setStrokeColor(theme.rule)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, y - theme.size_small - 3, PAGE_WIDTH - MARGIN, y - theme.size_small - 3)
    return y - theme.size_small - SECTION_LABEL_GAP


def _draw_header(
    canvas: pdfcanvas.Canvas,
    theme: Theme,
    analysis: DeckAnalysis,
    y: float,
) -> float:
    settings = theme.settings
    badge_width = 92.0
    title_width = theme.content_width - badge_width - GUTTER

    canvas.setFillColor(theme.text)
    canvas.setFont(theme.bold_font, theme.size_title)
    canvas.drawString(
        MARGIN,
        y - theme.size_title,
        fit_line(
            analysis.company or "Company not identified",
            theme.bold_font,
            theme.size_title,
            title_width,
        ),
    )
    line_y = y - theme.size_title - theme.size_body - 3

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
    band = settings.band_label(analysis.grounding_score)
    badge_x = PAGE_WIDTH - MARGIN - badge_width
    badge_top = y
    canvas.setFillColor(theme.band_color(analysis.grounding_score))
    canvas.rect(badge_x, badge_top - 34, badge_width, 34, stroke=0, fill=1)
    canvas.setFillColor(theme.background)
    canvas.setFont(theme.bold_font, 15)
    canvas.drawCentredString(
        badge_x + badge_width / 2, badge_top - 17, f"{analysis.grounding_score}/100"
    )
    canvas.setFont(theme.body_font, theme.size_small)
    canvas.drawCentredString(badge_x + badge_width / 2, badge_top - 29, band)

    return y - HEADER_HEIGHT


def _draw_tiles(
    canvas: pdfcanvas.Canvas,
    theme: Theme,
    analysis: DeckAnalysis,
    y: float,
) -> float:
    tiles = TILES[: theme.settings.render.max_metric_tiles]
    count = len(tiles)
    width = (theme.content_width - TILE_GAP * (count - 1)) / count

    x = MARGIN
    for name, label in tiles:
        metric = analysis.primary(name)
        value = fmt_metric(metric.value, metric.unit) if metric else ""
        canvas.setLineWidth(0.5)
        if value:
            canvas.setStrokeColor(theme.rule)
            canvas.setDash()
        else:
            canvas.setStrokeColor(theme.rule)
            canvas.setDash(2, 2)
        canvas.rect(x, y - TILE_HEIGHT, width, TILE_HEIGHT, stroke=1, fill=0)
        canvas.setDash()

        canvas.setFillColor(theme.muted)
        canvas.setFont(theme.body_font, theme.size_small)
        canvas.drawString(
            x + 5, y - 11, fit_line(label, theme.body_font, theme.size_small, width - 10)
        )

        canvas.setFillColor(theme.text if value else theme.muted)
        canvas.setFont(theme.bold_font if value else theme.body_font, theme.size_section)
        shown = value or theme.settings.render.not_disclosed_label
        canvas.drawString(
            x + 5, y - 24, fit_line(shown, theme.bold_font, theme.size_section, width - 10)
        )
        x += width + TILE_GAP

    return y - TILE_HEIGHT


def _draw_chart(canvas: pdfcanvas.Canvas, theme: Theme, png: bytes, y: float) -> float:
    y = _section_label(canvas, theme, "Traction — solid = actual, hatched = projected", y)
    image = ImageReader(BytesIO(png))
    raw_width, raw_height = image.getSize()
    width, height = float(raw_width), float(raw_height)
    scale = min(theme.content_width / width, CHART_HEIGHT / height)
    canvas.drawImage(
        image,
        MARGIN,
        y - height * scale,
        width=width * scale,
        height=height * scale,
        mask="auto",
    )
    return y - height * scale


def _draw_flags(
    canvas: pdfcanvas.Canvas,
    theme: Theme,
    settings: Settings,
    analysis: DeckAnalysis,
    plan: _Plan,
    y: float,
) -> float:
    y = _section_label(canvas, theme, "Red flags", y)
    if not plan.flags:
        # "No red flags" and "nothing could be read" must never look the same:
        # one is a result, the other is the absence of one.
        unread = analysis.nothing_extracted()
        message = settings.render.unread_label if unread else settings.render.no_flags_label
        canvas.setFillColor(theme.severity_color("critical") if unread else theme.muted)
        canvas.setFont(theme.body_font, theme.size_body)
        for line in wrap(
            " ".join(message.split()), theme.body_font, theme.size_body, theme.content_width
        ):
            y -= theme.size_body + LINE_GAP
            canvas.drawString(MARGIN, y, line)
        return y - LINE_GAP

    for flag in sorted(plan.flags, key=lambda f: (SEVERITY_RANK[f.severity], f.code)):
        colour = theme.severity_color(flag.severity)
        # Colour is never the only signal: the severity word rides in the chip.
        canvas.setFillColor(colour)
        canvas.rect(
            MARGIN, y - theme.size_section, CHIP_WIDTH, theme.size_section + 1, stroke=0, fill=1
        )
        canvas.setFillColor(theme.background)
        canvas.setFont(theme.bold_font, theme.size_small - 0.5)
        canvas.drawCentredString(
            MARGIN + CHIP_WIDTH / 2,
            y - theme.size_section + 2.5,
            flag.severity.upper(),
        )

        canvas.setFillColor(theme.text)
        canvas.setFont(theme.bold_font, theme.size_section)
        canvas.drawString(
            MARGIN + CHIP_WIDTH + 6,
            y - theme.size_section + 1,
            fit_line(
                flag.title,
                theme.bold_font,
                theme.size_section,
                theme.content_width - CHIP_WIDTH - 6,
            ),
        )
        y -= theme.size_section + 1.5

        canvas.setFillColor(theme.muted)
        canvas.setFont(theme.body_font, theme.size_body)
        for line in _flag_lines(theme, flag):
            y -= theme.size_body + LINE_GAP
            canvas.drawString(MARGIN + CHIP_WIDTH + 6, y, line)
        y -= FLAG_GAP

    if plan.dropped_flags:
        canvas.setFillColor(theme.muted)
        canvas.setFont(theme.body_font, theme.size_small)
        canvas.drawString(
            MARGIN,
            y - theme.size_small,
            settings.render.overflow_label.format(count=plan.dropped_flags),
        )
        y -= theme.size_small
    return y


def _draw_grounding(
    canvas: pdfcanvas.Canvas,
    theme: Theme,
    items: list[GroundingItem],
    y: float,
) -> float:
    y = _section_label(canvas, theme, "Needs grounding", y)
    if not items:
        canvas.setFillColor(theme.muted)
        canvas.setFont(theme.body_font, theme.size_body)
        canvas.drawString(MARGIN, y - theme.size_body, "Nothing outstanding.")
        return y - theme.size_body - LINE_GAP

    for item in items:
        canvas.setFillColor(theme.severity_color(item.severity))
        canvas.circle(MARGIN + 2, y - theme.size_body + 2.5, 1.8, stroke=0, fill=1)
        canvas.setFillColor(theme.text)
        canvas.setFont(theme.body_font, theme.size_body)
        for index, line in enumerate(
            wrap(_grounding_text(item), theme.body_font, theme.size_body, theme.content_width - 10)
        ):
            y -= theme.size_body + LINE_GAP
            canvas.drawString(MARGIN + 10, y, line)
            del index
        y -= 2.0
    return y


def _draw_gaps(
    canvas: pdfcanvas.Canvas,
    theme: Theme,
    analysis: DeckAnalysis,
    y: float,
) -> float:
    y = _section_label(canvas, theme, "Missing from the model", y)
    canvas.setFillColor(theme.muted)
    canvas.setFont(theme.body_font, theme.size_body)
    for line in wrap(_gaps_text(analysis), theme.body_font, theme.size_body, theme.content_width):
        y -= theme.size_body + LINE_GAP
        canvas.drawString(MARGIN, y, line)
    return y


def _draw_footer(
    canvas: pdfcanvas.Canvas,
    theme: Theme,
    settings: Settings,
    analysis: DeckAnalysis,
    sources: list[str],
) -> None:
    canvas.setStrokeColor(theme.rule)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, FOOTER_HEIGHT, PAGE_WIDTH - MARGIN, FOOTER_HEIGHT)

    canvas.setFillColor(theme.muted)
    canvas.setFont(theme.body_font, theme.size_small)

    # The run timestamp lives here and nowhere else: the JSON payload stays byte-stable.
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    source_line = "Sources: " + (", ".join(sources) if sources else "none") + f"  ·  Run {stamp}"
    canvas.drawString(
        MARGIN,
        FOOTER_HEIGHT - 11,
        fit_line(source_line, theme.body_font, theme.size_small, theme.content_width),
    )

    method_line = " ".join(analysis.methodology)[:400]
    if method_line:
        canvas.drawString(
            MARGIN,
            FOOTER_HEIGHT - 21,
            fit_line(method_line, theme.body_font, theme.size_small, theme.content_width),
        )

    canvas.drawString(
        MARGIN,
        FOOTER_HEIGHT - 31,
        fit_line(
            " ".join(settings.render.disclaimer.split()),
            theme.body_font,
            theme.size_small,
            theme.content_width,
        ),
    )
