"""Shared fixtures: build extraction payloads without calling the API.

Rule tests go through the real merge layer rather than hand-building a
``DeckAnalysis``, so provenance, unit handling, and the actual/projected split
are exercised the same way a live run exercises them.
"""

from __future__ import annotations

from typing import Any

import pytest

from deckscan.analyze.engine import run as run_rules
from deckscan.config import clear_config_cache, load_config
from deckscan.extract.schema import CLAIM_FIELDS
from deckscan.models import DeckAnalysis
from deckscan.parse.merge import build_analysis
from deckscan.render.narrative import analysis_fields


@pytest.fixture(scope="session")
def settings():
    clear_config_cache()
    return load_config()


@pytest.fixture(scope="session")
def fields():
    return analysis_fields()


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """Tests never hit the API; an ambient key must not change their behaviour."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def make_payload(
    *,
    narrative: dict[str, str] | None = None,
    sector: str = "B2B SaaS",
    stage: str = "seed",
    deck_date: str = "2026-01-15",
    periods: list[tuple[str, str]] | None = None,
    series: list[tuple[str, str, str]] | None = None,
    metrics: list[tuple[str, str, str]] | None = None,
    claims: dict[str, bool] | None = None,
    locator: str = "p.7",
    method: str = "table",
) -> dict[str, Any]:
    """Build one extraction payload.

    ``series`` items are ``(name, period, value)``; ``metrics`` are
    ``(name, value, unit)``.
    """
    all_claims = dict.fromkeys(CLAIM_FIELDS, False)
    all_claims["claims_financial_rigor"] = True
    all_claims.update(claims or {})

    return {
        "narrative": dict.fromkeys(analysis_fields(), "") | (narrative or {}),
        "sector": sector,
        "stage_guess": stage,
        "deck_date": deck_date,
        "periods": [
            {"label": label, "kind": kind, "locator": locator} for label, kind in (periods or [])
        ],
        "series": [
            {
                "name": name,
                "period": period,
                "value": value,
                "unit": "USD",
                "locator": locator,
                "method": method,
            }
            for name, period, value in (series or [])
        ],
        "metrics": [
            {
                "name": name,
                "value": value,
                "unit": unit,
                "period": "",
                "snippet": "",
                "locator": locator,
                "method": method,
            }
            for name, value, unit in (metrics or [])
        ],
        "claims": all_claims,
    }


def analyze(
    settings, fields, deck: dict[str, Any], model: dict[str, Any] | None = None
) -> DeckAnalysis:
    """Merge payload(s) and run the whole rule engine over the result."""
    analysis = build_analysis(
        settings=settings,
        narrative_fields=fields,
        deck_payload=deck,
        model_payload=model,
    )
    return run_rules(analysis, settings)


def codes(analysis: DeckAnalysis) -> set[str]:
    return {flag.code for flag in analysis.flags}


def gap_fields(analysis: DeckAnalysis) -> set[str]:
    return {gap.field for gap in analysis.gaps}
