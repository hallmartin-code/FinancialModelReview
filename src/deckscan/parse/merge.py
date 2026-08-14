"""Fold extraction payloads into a single :class:`DeckAnalysis`.

Two rules govern the merge:

* **The model is authoritative.** Where a financial model supplies a series, its
  periods replace the deck's for that series.
* **Nothing is discarded.** Every extracted instance of a metric survives as its
  own :class:`Metric` with its own provenance, so the consistency rules can see
  a deck figure and a model figure disagree instead of one silently winning.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from deckscan.config import Settings
from deckscan.extract.schema import CLAIM_FIELDS
from deckscan.models import DeckAnalysis, Metric, Provenance
from deckscan.parse.normalize import clean_text, to_decimal

Source = Literal["deck", "model"]
_METHODS = {"text", "table", "ocr", "cell"}


def _method(raw: str, default: str) -> Literal["text", "table", "ocr", "cell"]:
    value = (raw or "").strip().lower()
    chosen = value if value in _METHODS else default
    # Narrow for the type checker; membership above guarantees the literal set.
    if chosen == "table":
        return "table"
    if chosen == "cell":
        return "cell"
    if chosen == "ocr":
        return "ocr"
    return "text"


def _provenance(
    settings: Settings,
    source: Source,
    locator: str,
    method_raw: str,
    snippet: str | None = None,
) -> Provenance:
    method = _method(method_raw, "cell" if source == "model" else "text")
    confidence = settings.extraction.confidence_by_method.get(method, 0.5)
    return Provenance(
        source=source,
        locator=clean_text(locator) or ("model" if source == "model" else "deck"),
        snippet=clean_text(snippet) or None,
        method=method,
        confidence=confidence,
    )


def _items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _add_metric(
    metrics: dict[str, list[Metric]],
    name: str,
    value: Decimal | None,
    unit: str,
    period: str,
    provenance: Provenance,
) -> None:
    if value is None:
        return
    metrics.setdefault(name, []).append(
        Metric(
            name=name,
            value=value,
            unit=clean_text(unit) or None,
            period=clean_text(period) or None,
            provenance=[provenance],
        )
    )


def build_analysis(
    settings: Settings,
    narrative_fields: dict[str, str],
    deck_payload: dict[str, Any] | None,
    model_payload: dict[str, Any] | None,
    company_override: str | None = None,
) -> DeckAnalysis:
    """Build the analysis from zero, one, or two extraction payloads."""
    analysis = DeckAnalysis()
    payloads: list[tuple[Source, dict[str, Any]]] = []
    if deck_payload is not None:
        payloads.append(("deck", deck_payload))
    if model_payload is not None:
        payloads.append(("model", model_payload))

    # --- narrative and header fields (deck only; a model has no narrative) ----
    narrative: dict[str, str] = dict.fromkeys(narrative_fields, "")
    if deck_payload is not None:
        raw = deck_payload.get("narrative")
        if isinstance(raw, dict):
            for field in narrative_fields:
                narrative[field] = clean_text(raw.get(field))
    analysis.narrative = narrative
    analysis.company = company_override or narrative.get("company_name") or None
    analysis.tagline = narrative.get("tagline") or None

    for source, payload in payloads:
        if not analysis.sector:
            analysis.sector = clean_text(payload.get("sector")) or None
        if not analysis.stage_guess:
            analysis.stage_guess = clean_text(payload.get("stage_guess")).lower() or None
        if not analysis.deck_date:
            analysis.deck_date = clean_text(payload.get("deck_date")) or None
        del source

    # --- periods -------------------------------------------------------------
    actual: list[str] = []
    projected: list[str] = []
    for _, payload in payloads:
        for item in _items(payload, "periods"):
            label = clean_text(item.get("label"))
            if not label:
                continue
            bucket = actual if item.get("kind") == "actual" else projected
            if label not in bucket:
                bucket.append(label)
    # A period called both actual and projected is treated as actual: the proven
    # side of the boundary is the conservative reading.
    projected = [p for p in projected if p not in actual]
    analysis.actual_periods = actual
    analysis.projected_periods = projected

    # --- series and metrics --------------------------------------------------
    metrics: dict[str, list[Metric]] = {}
    series_by_source: dict[Source, dict[str, list[tuple[str, Decimal]]]] = {
        "deck": {},
        "model": {},
    }

    for source, payload in payloads:
        for item in _items(payload, "series"):
            name = clean_text(item.get("name")).lower()
            period = clean_text(item.get("period"))
            value = to_decimal(item.get("value"), settings)
            if not name or value is None:
                continue
            provenance = _provenance(
                settings, source, item.get("locator", ""), item.get("method", "")
            )
            if period:
                series_by_source[source].setdefault(name, []).append((period, value))
            # Every by-period figure is also a metric instance, so the consistency
            # rules can compare a deck figure against a model figure period by period.
            _add_metric(metrics, name, value, item.get("unit", ""), period, provenance)

        for item in _items(payload, "metrics"):
            name = clean_text(item.get("name")).lower()
            value = to_decimal(item.get("value"), settings)
            if not name or value is None:
                continue
            provenance = _provenance(
                settings,
                source,
                item.get("locator", ""),
                item.get("method", ""),
                item.get("snippet", ""),
            )
            _add_metric(
                metrics, name, value, item.get("unit", ""), item.get("period", ""), provenance
            )

    analysis.metrics = dict(sorted(metrics.items()))
    analysis.primary_metric = {
        name: _primary_index(values) for name, values in analysis.metrics.items()
    }

    ordered = analysis.actual_periods + analysis.projected_periods
    merged: dict[str, list[tuple[str, Decimal]]] = {}
    for name in sorted(set(series_by_source["deck"]) | set(series_by_source["model"])):
        # Model wins wholesale for a series it covers; mixing two sources inside one
        # series would produce a growth curve that exists in neither document.
        chosen = series_by_source["model"].get(name) or series_by_source["deck"].get(name) or []
        deduped: dict[str, Decimal] = {}
        for period, value in chosen:
            deduped.setdefault(period, value)
        merged[name] = sorted(
            deduped.items(),
            key=lambda pair: (
                ordered.index(pair[0]) if pair[0] in ordered else len(ordered),
                pair[0],
            ),
        )
    analysis.series = merged

    if "raise_ask" in analysis.metrics:
        analysis.raise_ask = analysis.primary("raise_ask")

    # --- section-presence claims (true if any source has it) -----------------
    claims = dict.fromkeys(CLAIM_FIELDS, False)
    for _, payload in payloads:
        raw = payload.get("claims")
        if isinstance(raw, dict):
            for field in CLAIM_FIELDS:
                claims[field] = claims[field] or bool(raw.get(field))
    analysis.claims = claims

    return analysis


def _primary_index(values: list[Metric]) -> int:
    """Prefer a model-sourced, period-free figure; fall back to the first entry."""
    for index, metric in enumerate(values):
        if metric.period is None and any(p.source == "model" for p in metric.provenance):
            return index
    for index, metric in enumerate(values):
        if metric.period is None:
            return index
    return 0
