"""Orchestration: file paths in, a :class:`DeckAnalysis` and two PDFs out.

This is the one place that knows the order of the layers::

    extract (Claude)  ->  parse/merge  ->  analyze (deterministic rules)  ->  render

Claude reads the documents; it does not decide anything. Every flag on the
financial screen is computed in Python from the extracted figures, so the same
extraction always produces the same verdicts.

A run needs at least one source and accepts two: a deck, a spreadsheet model, or
both. A spreadsheet on its own is a complete run — the financial screen is
computed from its figures exactly as it would be from a deck's, and the
narrative one-pager falls back to whatever prose the workbook itself carries.

Nothing here raises on source *content*: an extractor failure is degraded to a
recorded gap plus a methodology note, and the run still produces a report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from deckscan.analyze.engine import run as run_rules
from deckscan.analyze.rules.support import make_gap
from deckscan.config import Settings
from deckscan.extract.claude import ExtractionError, api_key_present, extract
from deckscan.extract.source import SourceContent, prepare_deck, prepare_model
from deckscan.models import DeckAnalysis, sort_gaps
from deckscan.parse.merge import build_analysis
from deckscan.render.narrative import analysis_fields, render_narrative
from deckscan.render.onepager import render_onepager

OcrMode = Literal["auto", "always", "never"]

#: PDFs are read natively by Claude, including image-only pages. PPTX and DOCX
#: are flattened to text first, so pictures inside them are not read.
DECK_SUFFIXES = frozenset({".pdf", ".pptx", ".docx"})
#: Spreadsheets are flattened to text with a cell reference on every value.
MODEL_SUFFIXES = frozenset({".xlsx", ".xlsm", ".csv"})
#: Anything the pipeline will accept as the source argument.
SOURCE_SUFFIXES = DECK_SUFFIXES | MODEL_SUFFIXES
#: Formats worth naming in the error rather than leaving to the generic list.
_HINTS = {
    ".xls": "legacy .xls is not supported; re-save the workbook as .xlsx",
    ".ppt": "legacy .ppt is not supported; re-save the deck as .pptx or export it as PDF",
    ".doc": "legacy .doc is not supported; re-save the document as .docx",
    ".key": "Keynote is not supported; export the deck as PDF",
    ".numbers": "Numbers is not supported; export the model as .xlsx or .csv",
}


class InputError(Exception):
    """Unrecoverable input problem. Maps to exit code 1."""


@dataclass(frozen=True)
class AnalysisRequest:
    """A validated request to analyze a deck, a model, or both.

    At least one of ``deck_path`` and ``model_path`` is set; :func:`resolve_inputs`
    is what guarantees it.
    """

    deck_path: Path | None
    model_path: Path | None
    company_override: str | None
    ocr_mode: OcrMode
    settings: Settings


@dataclass
class AnalysisResult:
    """The analysis plus the paths of everything written for it."""

    analysis: DeckAnalysis
    onepager_path: Path | None = None
    narrative_path: Path | None = None
    json_path: Path | None = None
    sources: list[str] = field(default_factory=list)


def _check_readable(path: Path, noun: str) -> None:
    if not path.exists():
        raise InputError(f"{noun} not found: {path}")
    if not path.is_file():
        raise InputError(f"{noun} is not a file: {path}")


def _unsupported(suffix: str, noun: str, allowed: frozenset[str]) -> InputError:
    hint = _HINTS.get(suffix)
    if hint:
        return InputError(hint)
    return InputError(
        f"unsupported {noun} type {suffix!r}; expected one of {', '.join(sorted(allowed))}"
    )


def resolve_inputs(
    source_path: Path, model_path: Path | None = None
) -> tuple[Path | None, Path | None]:
    """Sort one or two given files into ``(deck, model)``, or raise :class:`InputError`.

    The source argument may be a deck *or* a spreadsheet. A spreadsheet given
    there becomes the model and the run has no deck, which is a complete run in
    its own right — so this returns ``(None, model)`` rather than rejecting it.
    """
    suffix = source_path.suffix.lower()
    if suffix in MODEL_SUFFIXES:
        _check_readable(source_path, "model")
        if model_path is not None:
            raise InputError(
                f"two spreadsheets were given ({source_path.name} and {model_path.name}) "
                "and no deck; pass one of them as the source argument on its own"
            )
        return None, source_path

    _check_readable(source_path, "deck")
    if suffix not in DECK_SUFFIXES:
        raise _unsupported(suffix, "input", SOURCE_SUFFIXES)

    if model_path is not None:
        _check_readable(model_path, "model")
        model_suffix = model_path.suffix.lower()
        if model_suffix not in MODEL_SUFFIXES:
            raise _unsupported(model_suffix, "model", MODEL_SUFFIXES)
    return source_path, model_path


def _read_source(
    content: SourceContent,
    settings: Settings,
    fields: dict[str, str],
    notes: list[str],
) -> dict[str, Any] | None:
    """Extract one source, degrading any failure to a note rather than an exception."""
    notes.extend(content.methodology)
    if content.failed or content.is_empty:
        return None
    try:
        return extract(content, settings, fields)
    except ExtractionError as exc:
        notes.append(f"Extraction failed: {exc}")
        return None


def run_analysis(request: AnalysisRequest) -> DeckAnalysis:
    """Run the full pipeline for one request."""
    settings = request.settings
    fields = analysis_fields()
    notes: list[str] = []

    deck_payload: dict[str, Any] | None = None
    model_payload: dict[str, Any] | None = None

    if not api_key_present():
        notes.append("ANTHROPIC_API_KEY is not set, so no extraction was attempted.")
    else:
        if request.deck_path is not None:
            deck_content = prepare_deck(request.deck_path, settings)
            deck_payload = _read_source(deck_content, settings, fields, notes)

        if request.model_path is not None:
            model_content = prepare_model(request.model_path)
            model_payload = _read_source(model_content, settings, fields, notes)

    if request.model_path is None:
        notes.append("No financial model supplied; the deck is the only source.")
    elif request.deck_path is None:
        notes.append(
            f"No pitch deck supplied; the financial model {request.model_path.name} is the "
            "only source, so narrative sections carry only what the workbook itself states."
        )
    elif model_payload is not None:
        notes.append(
            f"Financial model {request.model_path.name} is authoritative where it and "
            "the deck disagree."
        )

    analysis = build_analysis(
        settings=settings,
        narrative_fields=fields,
        deck_payload=deck_payload,
        model_payload=model_payload,
        company_override=request.company_override,
    )
    analysis.methodology = notes

    analysis = run_rules(analysis, settings)

    # An extraction failure is judged per run, not per deck: a model-only run that
    # read its workbook is grounded, and a deck run whose deck failed is not.
    if deck_payload is None and model_payload is None:
        analysis.gaps = sort_gaps(
            [*analysis.gaps, make_gap(settings, "extraction_failure", load_bearing=True)]
        )
        analysis.grounding_score = min(
            analysis.grounding_score, settings.scoring.bands.needs_grounding_min - 1
        )

    analysis.methodology.append(
        "Figures were read by Claude and every flag was computed deterministically "
        "in Python from those figures."
    )
    return analysis


def render_outputs(
    analysis: DeckAnalysis,
    settings: Settings,
    onepager_path: Path,
    narrative_path: Path,
    sources: list[str],
) -> tuple[Path, Path]:
    """Write both PDFs and return their paths."""
    render_onepager(analysis, onepager_path, settings, sources=sources)
    render_narrative(analysis, narrative_path, settings=settings)
    return onepager_path, narrative_path


def default_output_paths(source_path: Path, directory: Path | None = None) -> tuple[Path, Path]:
    """``<stem>-screen.pdf`` and ``<stem>-onepager.pdf``."""
    target = directory or Path.cwd()
    return (
        target / f"{source_path.stem}-screen.pdf",
        target / f"{source_path.stem}-onepager.pdf",
    )
