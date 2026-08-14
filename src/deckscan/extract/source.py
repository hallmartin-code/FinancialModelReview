"""Turn an input file into content blocks Claude can read.

PDFs are sent as native document blocks so charts, tables, and image-only slides
are read from the page itself rather than from a lossy text dump. PPTX and
spreadsheets have no native block type, so they are flattened to text with
slide/cell markers that survive into the locators.

Nothing here interprets meaning, and nothing here raises on bad input: an
unreadable source degrades to an empty block list plus a methodology note, which
the pipeline turns into a recorded gap.
"""

from __future__ import annotations

import base64
import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deckscan.config import Settings

#: Anthropic caps a request at 32MB; stay under it after base64 expansion (~1.34x).
MAX_NATIVE_PDF_BYTES = 20 * 1024 * 1024
#: Native PDF input is capped at 100 pages for 200k-context models; stay well inside.
MAX_NATIVE_PDF_PAGES = 100
#: Hard ceiling on inlined text, so a pathological file cannot blow up the request.
MAX_TEXT_CHARS = 400_000


@dataclass
class SourceContent:
    """One prepared input: what to send, and what to say about how it was read."""

    label: str
    """Human phrase used in the prompt, e.g. 'pitch deck (PDF)'."""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    inline_text: str | None = None
    methodology: list[str] = field(default_factory=list)
    failed: bool = False
    """True when nothing readable came out of the file."""

    @property
    def is_empty(self) -> bool:
        return not self.blocks and not (self.inline_text or "").strip()


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS] + "\n\n[source truncated]", True


def prepare_deck(path: Path, settings: Settings) -> SourceContent:
    """Prepare a .pdf or .pptx deck."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _prepare_pdf(path, settings)
    if suffix == ".pptx":
        return _prepare_pptx(path)
    if suffix == ".docx":
        return _prepare_docx(path)
    return SourceContent(
        label="pitch deck",
        methodology=[f"Unsupported deck type {suffix!r}; nothing was read."],
        failed=True,
    )


def prepare_model(path: Path) -> SourceContent:
    """Prepare a .xlsx or .csv financial model."""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return _prepare_xlsx(path)
    if suffix == ".csv":
        return _prepare_csv(path)
    return SourceContent(
        label="financial model",
        methodology=[f"Unsupported model type {suffix!r}; nothing was read."],
        failed=True,
    )


# --- PDF ---------------------------------------------------------------------


def _pdf_text(path: Path) -> tuple[str, int, int]:
    """Extracted text with page markers, page count, and pages that had no text."""
    import pdfplumber

    chunks: list[str] = []
    pages = 0
    empty = 0
    with pdfplumber.open(str(path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            pages += 1
            text = (page.extract_text() or "").strip()
            if not text:
                empty += 1
                continue
            chunks.append(f"[p.{index}]\n{text}")
    return "\n\n".join(chunks), pages, empty


def _prepare_pdf(path: Path, settings: Settings) -> SourceContent:
    content = SourceContent(label="pitch deck (PDF)")

    try:
        text, pages, empty_pages = _pdf_text(path)
    except Exception as exc:  # pdfplumber raises a wide range on damaged files
        text, pages, empty_pages = "", 0, 0
        content.methodology.append(f"PDF text layer unreadable ({type(exc).__name__}).")

    size = path.stat().st_size
    native = size <= MAX_NATIVE_PDF_BYTES and 0 < pages <= MAX_NATIVE_PDF_PAGES

    if native:
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        content.blocks.append(
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                "title": path.name,
            }
        )
        content.methodology.append(
            f"Deck read as a native PDF ({pages} pages), so charts and image-only "
            "pages were read from the page itself."
        )
        if empty_pages:
            content.methodology.append(
                f"{empty_pages} of {pages} pages carry no text layer and were read visually."
            )
        return content

    if pages > MAX_NATIVE_PDF_PAGES:
        content.methodology.append(
            f"Deck has {pages} pages, above the {MAX_NATIVE_PDF_PAGES}-page native limit; "
            "extracted text was sent instead and any image-only pages were not read."
        )
    elif size > MAX_NATIVE_PDF_BYTES:
        content.methodology.append(
            f"Deck is {size // (1024 * 1024)}MB, above the "
            f"{MAX_NATIVE_PDF_BYTES // (1024 * 1024)}MB native limit; extracted text "
            "was sent instead and any image-only pages were not read."
        )

    if not text.strip():
        content.failed = True
        content.methodology.append("No text could be extracted from the deck.")
        return content

    body, truncated = _truncate(text)
    content.inline_text = body
    if truncated:
        content.methodology.append("Deck text was truncated to fit the request.")
    if empty_pages and pages:
        content.methodology.append(
            f"{empty_pages} of {pages} pages carry no text layer and were not read. "
            f"(Threshold: {settings.extraction.min_chars_per_page} chars per page.)"
        )
    return content


# --- PPTX --------------------------------------------------------------------


def _shape_text(shape: Any) -> list[str]:
    """Text from one shape, recursing into groups and reading table cells."""
    out: list[str] = []
    if getattr(shape, "shape_type", None) == 6:  # MSO_SHAPE_TYPE.GROUP
        for child in shape.shapes:
            out.extend(_shape_text(child))
        return out
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            line = " | ".join(c for c in cells if c)
            if line:
                out.append(line)
        return out
    if getattr(shape, "has_text_frame", False):
        for para in shape.text_frame.paragraphs:
            line = "".join(run.text for run in para.runs).strip()
            if line:
                out.append(line)
        return out
    text = getattr(shape, "text", "")
    if isinstance(text, str) and text.strip():
        out.append(text.strip())
    return out


def _prepare_pptx(path: Path) -> SourceContent:
    content = SourceContent(label="pitch deck (PowerPoint)")
    try:
        from pptx import Presentation

        prs = Presentation(str(path))
    except Exception as exc:
        content.failed = True
        content.methodology.append(f"PowerPoint file could not be opened ({type(exc).__name__}).")
        return content

    chunks: list[str] = []
    picture_only = 0
    for index, slide in enumerate(prs.slides, start=1):
        lines: list[str] = []
        for shape in slide.shapes:
            lines.extend(_shape_text(shape))
        notes = ""
        if slide.has_notes_slide:
            notes = (slide.notes_slide.notes_text_frame.text or "").strip()
        if not lines and not notes:
            picture_only += 1
            continue
        body = "\n".join(lines)
        if notes:
            body += f"\n[speaker notes] {notes}"
        chunks.append(f"[slide {index}]\n{body}")

    if not chunks:
        content.failed = True
        content.methodology.append("No text could be extracted from any slide.")
        return content

    text, truncated = _truncate("\n\n".join(chunks))
    content.inline_text = text
    content.methodology.append(f"Deck read as PowerPoint text ({len(chunks)} slides with text).")
    if picture_only:
        content.methodology.append(
            f"{picture_only} slides contain only images and could not be read; "
            "supply the deck as a PDF to have those pages read visually."
        )
    if truncated:
        content.methodology.append("Deck text was truncated to fit the request.")
    return content


# --- DOCX --------------------------------------------------------------------


def _prepare_docx(path: Path) -> SourceContent:
    """Walk the document body in order, splitting on explicit page breaks.

    Decks are sometimes written as Word documents. Page breaks are the closest
    thing the format has to slide boundaries, so they become the locator unit.
    """
    content = SourceContent(label="pitch deck (Word)")
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = Document(str(path))
    except Exception as exc:
        content.failed = True
        content.methodology.append(f"Word document could not be opened ({type(exc).__name__}).")
        return content

    def has_page_break(element: Any) -> bool:
        if any(node.get(qn("w:type")) == "page" for node in element.iter(qn("w:br"))):
            return True
        # Word writes this where it laid out a page.
        return next(element.iter(qn("w:lastRenderedPageBreak")), None) is not None

    pages: list[list[str]] = [[]]
    for child in document.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            text = Paragraph(child, document).text.strip()
            if text:
                pages[-1].append(text)
            if has_page_break(child):
                pages.append([])
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells = [cell.text.strip() for cell in row.cells]
                line = " | ".join(c for c in cells if c)
                if line:
                    pages[-1].append(line)

    chunks = [
        f"[p.{index}]\n" + "\n".join(lines)
        for index, lines in enumerate((p for p in pages if p), start=1)
    ]
    if not chunks:
        content.failed = True
        content.methodology.append("No text could be extracted from the document.")
        return content

    text, truncated = _truncate("\n\n".join(chunks))
    content.inline_text = text
    content.methodology.append(
        f"Deck read as Word text ({len(chunks)} page-equivalents); any embedded "
        "images were not read."
    )
    if truncated:
        content.methodology.append("Deck text was truncated to fit the request.")
    return content


# --- spreadsheets ------------------------------------------------------------


def _column_letter(index: int) -> str:
    """1 -> A, 27 -> AA."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _prepare_xlsx(path: Path) -> SourceContent:
    content = SourceContent(label="financial model (Excel)")
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(str(path), data_only=True, read_only=True)
    except Exception as exc:
        content.failed = True
        content.methodology.append(f"Workbook could not be opened ({type(exc).__name__}).")
        return content

    chunks: list[str] = []
    for sheet in workbook.worksheets:
        rows: list[str] = []
        for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            cells = [
                f"{_column_letter(col)}{row_index}={value}"
                for col, value in enumerate(row, start=1)
                if value is not None and str(value).strip() != ""
            ]
            if cells:
                rows.append("  ".join(cells))
        if rows:
            chunks.append(f"[sheet {sheet.title}]\n" + "\n".join(rows))
    workbook.close()

    if not chunks:
        content.failed = True
        content.methodology.append("The workbook contained no populated cells.")
        return content

    text, truncated = _truncate("\n\n".join(chunks))
    content.inline_text = text
    content.methodology.append(
        f"Model read from {len(chunks)} sheet(s); every value carries its cell reference."
    )
    if truncated:
        content.methodology.append("Model text was truncated to fit the request.")
    return content


def _prepare_csv(path: Path) -> SourceContent:
    content = SourceContent(label="financial model (CSV)")
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(raw)))
    except Exception as exc:
        content.failed = True
        content.methodology.append(f"CSV could not be read ({type(exc).__name__}).")
        return content

    lines: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells = [
            f"{_column_letter(col)}{row_index}={value.strip()}"
            for col, value in enumerate(row, start=1)
            if value.strip()
        ]
        if cells:
            lines.append("  ".join(cells))

    if not lines:
        content.failed = True
        content.methodology.append("The CSV contained no populated cells.")
        return content

    text, truncated = _truncate(f"[sheet {path.stem}]\n" + "\n".join(lines))
    content.inline_text = text
    content.methodology.append("Model read from CSV; every value carries its cell reference.")
    if truncated:
        content.methodology.append("Model text was truncated to fit the request.")
    return content
