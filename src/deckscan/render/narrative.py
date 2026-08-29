"""The narrative one-pager, drawn from ``config/onepager_template.json``.

The template is the single source of truth for which fields exist, what each
section is labelled, and every colour and measurement on the page. This module
resolves that template into coordinates and draws it — it decides nothing.

A field the extractor did not find is empty, and an empty field's section is
omitted (``rules.empty_fields: omit_section``) rather than rendered blank.

The page carries the red-flag analysis in a band above the footer: the flags the
rule engine fired, and the ranked list of what would ground the claims. The
narrative above it is what the company says; the band is what the numbers show.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas

from deckscan.analyze.scoring import grounding_items
from deckscan.config import Settings, load_config
from deckscan.models import DeckAnalysis
from deckscan.render.text import ellipsize, fit_line, wrap

PAGE_W, PAGE_H = letter
TEMPLATE_RESOURCE = ("deckscan", "config/onepager_template.json")


class TemplateError(RuntimeError):
    """Raised when the document template is missing or malformed."""


REQUIRED_SECTIONS = (
    "analysis_fields",
    "page",
    "palette",
    "typography",
    "header",
    "body",
    "footer",
    "analysis",
)


@lru_cache(maxsize=4)
def _load(path: str | None) -> dict[str, Any]:
    if path:
        raw = Path(path).read_text(encoding="utf-8")
    else:
        raw = (
            resources.files(TEMPLATE_RESOURCE[0])
            .joinpath(TEMPLATE_RESOURCE[1])
            .read_text(encoding="utf-8")
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TemplateError(f"One-pager template is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateError("One-pager template must contain a JSON object.")
    missing = [key for key in REQUIRED_SECTIONS if key not in data]
    if missing:
        raise TemplateError(f"One-pager template is missing: {', '.join(missing)}")
    return data


def load_template(path: str | Path | None = None) -> dict[str, Any]:
    """The active document template."""
    return _load(str(path) if path else None)


def analysis_fields(path: str | Path | None = None) -> dict[str, str]:
    """Field name -> extraction guidance, for every field the document renders."""
    fields = load_template(path)["analysis_fields"]
    if not isinstance(fields, dict) or not fields:
        raise TemplateError("Template 'analysis_fields' must be a non-empty object.")
    return {str(key): str(value) for key, value in fields.items()}


# --- drawing -----------------------------------------------------------------


def _color(template: dict[str, Any], name: str) -> Color:
    try:
        return HexColor(template["palette"][name])
    except KeyError as exc:
        raise TemplateError(f"Template palette has no colour {name!r}.") from exc


def _font(template: dict[str, Any], name: str) -> str:
    typography = template["typography"]
    return str(typography.get(name, typography.get("body_font", "Helvetica")))


def _value(analysis: DeckAnalysis, field: str, placeholders: bool, placeholder: str) -> str:
    text = (analysis.narrative or {}).get(field, "").strip()
    if text:
        return text
    return placeholder if placeholders else ""


def render_narrative(
    analysis: DeckAnalysis,
    output_path: Path,
    template_path: str | Path | None = None,
    settings: Settings | None = None,
) -> Path:
    """Render the narrative one-pager for ``analysis`` at ``output_path``."""
    return _render(analysis, output_path, template_path, placeholders=False, settings=settings)


def render_template_skeleton(
    output_path: Path,
    template_path: str | Path | None = None,
) -> Path:
    """Render the empty structural template — labels and ``{{field}}`` placeholders only."""
    return _render(DeckAnalysis(), output_path, template_path, placeholders=True)


def _render(
    analysis: DeckAnalysis,
    output_path: Path,
    template_path: str | Path | None,
    *,
    placeholders: bool,
    settings: Settings | None = None,
) -> Path:
    template = load_template(template_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    name = analysis.company or ("{{company_name}}" if placeholders else "Company")
    canvas = pdfcanvas.Canvas(str(output_path), pagesize=letter)
    canvas.setTitle(f"{name} - Investor One-Pager")
    canvas.setAuthor(analysis.company or "")

    _draw_header(canvas, template, analysis, placeholders)
    _draw_body(canvas, template, analysis, placeholders)
    _draw_analysis(canvas, template, analysis, placeholders, settings or load_config())
    _draw_footer(canvas, template, analysis, placeholders)

    canvas.showPage()
    canvas.save()
    return output_path


def _draw_header(
    canvas: pdfcanvas.Canvas,
    template: dict[str, Any],
    analysis: DeckAnalysis,
    placeholders: bool,
) -> None:
    header = template["header"]
    height = float(header["height_pt"])
    margin = float(template["page"]["margin_pt"])

    canvas.setFillColor(_color(template, header["background"]))
    canvas.rect(0, PAGE_H - height, PAGE_W, height, stroke=0, fill=1)

    width = PAGE_W - 2 * margin
    for element in header["elements"]:
        text = _value(analysis, element["field"], placeholders, str(element.get("placeholder", "")))
        if not text and element.get("required") and placeholders:
            text = str(element.get("placeholder", ""))
        if not text:
            continue
        font = _font(template, element["font"])
        size = float(element["size_pt"])
        if element.get("single_line", True):
            text = fit_line(text, font, size, width)
        canvas.setFillColor(_color(template, element["color"]))
        canvas.setFont(font, size)
        canvas.drawString(margin, PAGE_H - float(element["baseline_from_top_pt"]), text)


def _draw_body(
    canvas: pdfcanvas.Canvas,
    template: dict[str, Any],
    analysis: DeckAnalysis,
    placeholders: bool,
) -> None:
    page, body = template["page"], template["body"]
    margin = float(page["margin_pt"])
    gutter = float(page["gutter_pt"])

    columns = body["columns"]
    usable = (PAGE_W - 2 * margin) - gutter * (len(columns) - 1)

    top_y = PAGE_H - float(template["header"]["height_pt"]) - float(body["top_padding_pt"])
    # The analysis band is reserved space, not something the narrative can eat into.
    bottom_y = (
        float(template["footer"]["height_pt"])
        + float(template["analysis"]["height_pt"])
        + float(body["bottom_padding_pt"])
    )
    available = top_y - bottom_y

    x = margin
    for column in columns:
        width = usable * float(column["width_ratio"])
        blocks = _layout_column(
            template, analysis, column["sections"], width, available, placeholders
        )
        _draw_column(canvas, template, x, top_y, width, blocks)
        x += width + gutter


def _layout_column(
    template: dict[str, Any],
    analysis: DeckAnalysis,
    sections: list[dict[str, Any]],
    width: float,
    available: float,
    placeholders: bool,
) -> list[tuple[str, list[str]]]:
    """Wrap each section to ``width``, then trim until the column fits ``available``."""
    typography = template["typography"]
    font = _font(template, "body_font")
    size = float(typography["content_size_pt"])

    blocks: list[tuple[str, list[str]]] = []
    for section in sections:
        text = _value(analysis, section["field"], placeholders, str(section.get("placeholder", "")))
        if not text:
            continue  # rules.empty_fields == "omit_section"
        lines = wrap(text, font, size, width)
        if lines:
            blocks.append((str(section["label"]), lines))

    while blocks and _column_height(template, blocks) > available:
        longest = max(range(len(blocks)), key=lambda i: len(blocks[i][1]))
        if len(blocks[longest][1]) > 1:
            label, lines = blocks[longest]
            kept = lines[:-1]
            kept[-1] = ellipsize(kept[-1], font, size, width)
            blocks[longest] = (label, kept)
        else:
            blocks.pop()
    return blocks


def _column_height(template: dict[str, Any], blocks: list[tuple[str, list[str]]]) -> float:
    typography = template["typography"]
    label_height = float(typography["label_size_pt"]) + float(typography["label_gap_pt"])
    leading = float(typography["content_leading_pt"])
    gap = float(typography["section_gap_pt"])
    total = sum(label_height + len(lines) * leading + gap for _, lines in blocks)
    return total - gap if blocks else 0.0


def _draw_column(
    canvas: pdfcanvas.Canvas,
    template: dict[str, Any],
    x: float,
    top_y: float,
    width: float,
    blocks: list[tuple[str, list[str]]],
) -> None:
    typography = template["typography"]
    label_font = _font(template, "bold_font")
    label_size = float(typography["label_size_pt"])
    body_font = _font(template, "body_font")
    content_size = float(typography["content_size_pt"])
    leading = float(typography["content_leading_pt"])

    y = top_y
    for label, lines in blocks:
        canvas.setFillColor(_color(template, "label_gray"))
        canvas.setFont(label_font, label_size)
        canvas.drawString(x, y - label_size, fit_line(label.upper(), label_font, label_size, width))
        y -= label_size + float(typography["label_gap_pt"])

        canvas.setFillColor(_color(template, "black"))
        canvas.setFont(body_font, content_size)
        for line in lines:
            y -= leading
            canvas.drawString(x, y, line)
        y -= float(typography["section_gap_pt"])


def _draw_analysis(
    canvas: pdfcanvas.Canvas,
    template: dict[str, Any],
    analysis: DeckAnalysis,
    placeholders: bool,
    settings: Settings,
) -> None:
    """Draw the red-flag band: what the numbers show, under what the company says.

    Flags are already ranked by severity by the engine. Only the top few fit here;
    the rest stay in the financial screen and the JSON, and the count says so
    rather than leaving the reader to assume this is all of them.
    """
    band = template["analysis"]
    margin = float(template["page"]["margin_pt"])
    height = float(band["height_pt"])
    pad = float(band["inner_padding_pt"])
    width = PAGE_W - 2 * margin

    top = float(template["footer"]["height_pt"]) + height
    canvas.setFillColor(_color(template, band["background"]))
    canvas.rect(0, float(template["footer"]["height_pt"]), PAGE_W, height, stroke=0, fill=1)
    canvas.setStrokeColor(_color(template, band["rule_color"]))
    canvas.setLineWidth(0.6)
    canvas.line(margin, top, PAGE_W - margin, top)

    typography = template["typography"]
    label_font = _font(template, "bold_font")
    body_font = _font(template, "body_font")
    label_size = float(typography["label_size_pt"])
    content_size = float(typography["content_size_pt"]) - 1
    leading = float(band["flag_leading_pt"])

    y = top - pad

    # Heading row: section label on the left, grounding score on the right.
    canvas.setFillColor(_color(template, "label_gray"))
    canvas.setFont(label_font, label_size)
    canvas.drawString(margin, y - label_size, str(band["label"]))

    if not placeholders:
        score = f"{band['score_label']} {analysis.grounding_score}/100 · "
        score += settings.band_label(analysis.grounding_score).upper()
        canvas.drawRightString(PAGE_W - margin, y - label_size, score)
    y -= label_size + float(typography["label_gap_pt"]) + 2

    # Flags.
    chip = float(band["chip_width_pt"])
    text_x = margin + chip + 6
    text_width = width - chip - 6

    if placeholders:
        rows: list[tuple[str, str, str]] = [
            ("high", "{{flag_title}}", str(band["placeholder_flag"]))
        ]
    else:
        rows = [
            (flag.severity, flag.title, flag.finding)
            for flag in analysis.flags[: int(band["max_flags"])]
        ]

    if not rows:
        canvas.setFillColor(_color(template, "label_gray"))
        canvas.setFont(body_font, content_size)
        canvas.drawString(margin, y - content_size, str(band["empty_label"]))
        y -= content_size + leading
    for severity, title, finding in rows:
        colour_key = band["severity_colors"].get(severity, "info")
        canvas.setFillColor(_color(template, colour_key))
        canvas.rect(margin, y - content_size - 1.5, chip, content_size + 2.5, stroke=0, fill=1)
        canvas.setFillColor(_color(template, "white"))
        canvas.setFont(label_font, label_size - 1.5)
        canvas.drawCentredString(margin + chip / 2, y - content_size + 1, severity.upper())

        canvas.setFillColor(_color(template, "black"))
        canvas.setFont(label_font, content_size)
        title_width = stringWidth(title + "  ", label_font, content_size)
        canvas.drawString(
            text_x, y - content_size, fit_line(title, label_font, content_size, text_width)
        )

        # The finding carries the numbers; it shares the line when there is room.
        remaining = text_width - title_width
        if remaining > 60:
            canvas.setFillColor(_color(template, "label_gray"))
            canvas.setFont(body_font, content_size)
            canvas.drawString(
                text_x + title_width,
                y - content_size,
                fit_line(finding, body_font, content_size, remaining),
            )
        y -= content_size + leading - 3

    dropped = max(0, len(analysis.flags) - int(band["max_flags"])) if not placeholders else 0
    if dropped:
        canvas.setFillColor(_color(template, "label_gray"))
        canvas.setFont(body_font, content_size - 1)
        canvas.drawString(
            text_x, y - content_size, str(band["overflow_label"]).format(count=dropped)
        )
        y -= content_size

    # Needs grounding.
    y -= 4
    canvas.setFillColor(_color(template, "label_gray"))
    canvas.setFont(label_font, label_size)
    canvas.drawString(margin, y - label_size, str(band["grounding_label"]))
    y -= label_size + float(typography["label_gap_pt"])

    if placeholders:
        items = [str(band["placeholder_grounding"])]
    else:
        items = [
            f"{item.claim} — {item.substantiation}"
            for item in grounding_items(analysis, settings)[: int(band["max_grounding_items"])]
        ]

    canvas.setFillColor(_color(template, "black"))
    canvas.setFont(body_font, content_size)
    for item in items:
        canvas.drawString(margin, y - content_size, "·")
        canvas.drawString(
            margin + 8, y - content_size, fit_line(item, body_font, content_size, width - 8)
        )
        y -= content_size + leading - 3.5


def _draw_footer(
    canvas: pdfcanvas.Canvas,
    template: dict[str, Any],
    analysis: DeckAnalysis,
    placeholders: bool,
) -> None:
    footer = template["footer"]
    margin = float(template["page"]["margin_pt"])
    height = float(footer["height_pt"])
    font = _font(template, footer["font"])
    size = float(footer["size_pt"])

    canvas.setFillColor(_color(template, footer["background"]))
    canvas.rect(0, 0, PAGE_W, height, stroke=0, fill=1)

    canvas.setFillColor(_color(template, footer["color"]))
    canvas.setFont(font, size)
    baseline = height / 2 - size / 2 + 1
    canvas.drawString(margin, baseline, str(footer["left_text"]))

    contact = _value(
        analysis,
        str(footer["right_field"]),
        placeholders,
        str(footer.get("right_placeholder", "")),
    ).replace("\n", " ")
    if contact:
        max_width = (PAGE_W - 2 * margin) * float(footer.get("right_max_width_ratio", 0.5))
        canvas.drawRightString(PAGE_W - margin, baseline, fit_line(contact, font, size, max_width))
