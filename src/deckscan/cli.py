"""Typer CLI: argument validation, human summary, exit codes.

Exit codes
----------
0
    An analysis was produced. A parseable-but-empty deck that yields a report
    full of gaps still exits 0.
1
    Unrecoverable input error: file missing, unsupported extension, unreadable
    container.
2
    ``--strict`` was passed and at least one CRITICAL flag fired.
"""

from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from deckscan import __version__
from deckscan.config import load_config
from deckscan.extract.claude import api_key_present
from deckscan.models import DeckAnalysis, canonical_json
from deckscan.pipeline import (
    AnalysisRequest,
    InputError,
    default_output_paths,
    render_outputs,
    run_analysis,
    validate_inputs,
)
from deckscan.render.narrative import render_template_skeleton

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_STRICT_CRITICAL = 2


class OcrChoice(StrEnum):
    auto = "auto"
    always = "always"
    never = "never"


app = typer.Typer(
    name="deckscan",
    add_completion=False,
    no_args_is_help=True,
    help="Pitch deck to investor one-pagers, with a deterministic financial red-flag scan.",
)


@app.callback()
def _root() -> None:
    """deckscan."""


def _echo_err(message: str) -> None:
    typer.echo(message, err=True)


def _print_summary(analysis: DeckAnalysis, paths: dict[str, Path | None]) -> None:
    counts = analysis.counts_by_severity()
    typer.echo(f"Company:  {analysis.company or 'not identified'}")
    typer.echo(f"Stage:    {analysis.stage_guess or 'not identified'}")
    typer.echo(f"Grounded: {analysis.grounding_score}/100")
    typer.echo(
        "Flags:    "
        + ", ".join(f"{count} {severity}" for severity, count in counts.items())
        + f"  |  gaps: {len(analysis.gaps)}"
    )
    for label, path in paths.items():
        if path is not None:
            typer.echo(f"{label:<9} {path}")


@app.command()
def analyze(
    deck_path: Annotated[Path, typer.Argument(help="Pitch deck: .pdf or .pptx")],
    model: Annotated[
        Path | None,
        typer.Option("--model", help="Financial model (.xlsx/.csv). Authoritative when present."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Financial screen PDF. Default: <deck-stem>-screen.pdf"),
    ] = None,
    narrative: Annotated[
        Path | None,
        typer.Option(
            "--narrative", help="Narrative one-pager PDF. Default: <deck-stem>-onepager.pdf"
        ),
    ] = None,
    json_out: Annotated[
        Path | None,
        typer.Option("--json", help="Also write the full DeckAnalysis as JSON."),
    ] = None,
    company: Annotated[
        str | None,
        typer.Option("--company", help="Override the extracted company name."),
    ] = None,
    ocr: Annotated[
        OcrChoice,
        typer.Option("--ocr", help="Reserved; PDFs are read natively, including scanned pages."),
    ] = OcrChoice.auto,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="YAML overriding the packaged rules.yaml."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit 2 if any CRITICAL flag fires."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Print methodology notes to stderr."),
    ] = False,
) -> None:
    """Analyze a pitch deck and emit both investor one-pagers."""
    try:
        validate_inputs(deck_path, model)
        settings = load_config(config)
    except InputError as exc:
        _echo_err(f"error: {exc}")
        raise typer.Exit(EXIT_INPUT_ERROR) from exc
    except (OSError, ValueError) as exc:
        _echo_err(f"error: could not load configuration: {exc}")
        raise typer.Exit(EXIT_INPUT_ERROR) from exc

    if not api_key_present():
        _echo_err(
            "warning: ANTHROPIC_API_KEY is not set - the report will be all gaps. "
            "Create a key at https://console.anthropic.com/settings/keys."
        )

    request = AnalysisRequest(
        deck_path=deck_path,
        model_path=model,
        company_override=company,
        ocr_mode=ocr.value,
        settings=settings,
    )
    analysis = run_analysis(request)

    default_screen, default_narrative = default_output_paths(deck_path)
    screen_path = out or default_screen
    narrative_path = narrative or default_narrative

    sources = [deck_path.name] + ([model.name] if model else [])
    render_outputs(analysis, settings, screen_path, narrative_path, sources)

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(canonical_json(analysis), encoding="utf-8")

    if verbose:
        for note in analysis.methodology:
            _echo_err(f"  {note}")

    _print_summary(
        analysis,
        {"Screen:": screen_path, "One-pager:": narrative_path, "JSON:": json_out},
    )

    if strict and any(flag.severity == "critical" for flag in analysis.flags):
        raise typer.Exit(EXIT_STRICT_CRITICAL)


@app.command()
def template(
    out: Annotated[
        Path,
        typer.Option("--out", help="Where to write the blank structural template."),
    ] = Path("onepager-template.pdf"),
) -> None:
    """Render the one-pager template with placeholders instead of company data."""
    render_template_skeleton(out)
    typer.echo(f"Template: {out}")


@app.command()
def version() -> None:
    """Print the deckscan version."""
    typer.echo(__version__)


def main() -> None:
    """Console entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
