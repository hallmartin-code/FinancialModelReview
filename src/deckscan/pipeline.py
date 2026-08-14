"""Orchestration: file paths in, a :class:`DeckAnalysis` and two PDFs out.

This is the one place that knows the order of the layers::

    extract (Claude)  ->  parse/merge  ->  analyze (deterministic rules)  ->  render

Claude reads the documents; it does not decide anything. Every flag on the
financial screen is computed in Python from the extracted figures, so the same
extraction always produces the same verdicts.

Nothing here raises on deck *content*: an extractor failure is degraded to a
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
MODEL_SUFFIXES = frozenset({".xlsx", ".csv"})


class InputError(Exception):
    """Unrecoverable input problem. Maps to exit code 1."""


@dataclass(frozen=True)
class AnalysisRequest:
    """A validated request to analyze one deck and optional model."""

    deck_path: Path
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


def validate_inputs(deck_path: Path, model_path: Path | None) -> None:
    """Raise :class:`InputError` for anything the pipeline cannot open at all."""
    if not deck_path.exists():
        raise InputError(f"deck not found: {deck_path}")
    if not deck_path.is_file():
        raise InputError(f"deck is not a file: {deck_path}")
    if deck_path.suffix.lower() not in DECK_SUFFIXES:
        raise InputError(
            f"unsupported deck type {deck_path.suffix!r}; expected one of "
            f"{', '.join(sorted(DECK_SUFFIXES))}"
        )
    if model_path is not None:
        if not model_path.exists():
            raise InputError(f"model not found: {model_path}")
        if not model_path.is_file():
            raise InputError(f"model is not a file: {model_path}")
        if model_path.suffix.lower() not in MODEL_SUFFIXES:
            raise InputError(
                f"unsupported model type {model_path.suffix!r}; expected one of "
                f"{', '.join(sorted(MODEL_SUFFIXES))}"
            )


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

    if not api_key_present():
        notes.append("ANTHROPIC_API_KEY is not set, so no extraction was attempted.")
        deck_payload = model_payload = None
    else:
        deck_content = prepare_deck(request.deck_path, settings)
        deck_payload = _read_source(deck_content, settings, fields, notes)

        model_payload = None
        if request.model_path is not None:
            model_content = prepare_model(request.model_path)
            model_payload = _read_source(model_content, settings, fields, notes)

    if request.model_path is None:
        notes.append("No financial model supplied; the deck is the only source.")
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

    if deck_payload is None:
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
    render_narrative(analysis, narrative_path)
    return onepager_path, narrative_path


def default_output_paths(deck_path: Path, directory: Path | None = None) -> tuple[Path, Path]:
    """``<stem>-screen.pdf`` and ``<stem>-onepager.pdf``."""
    target = directory or Path.cwd()
    return (
        target / f"{deck_path.stem}-screen.pdf",
        target / f"{deck_path.stem}-onepager.pdf",
    )
