"""Grounding score and the ranked "needs grounding" list.

Formula (all terms configurable in ``rules.yaml`` under ``scoring``)::

    score = 100
          + sum(flag_penalty[severity] for each flag)      # critical -15, high -8, medium -3
          + sum(gap_penalty[severity] for each gap)        # critical -12, others -4
          + round(provenance_bonus_max * provenance_density)
    score = max(0, min(100, score))

``provenance_density`` is the share of headline metrics whose authoritative value
was read from a table or a spreadsheet cell rather than from loose text or OCR —
it rewards figures that came from somewhere structured, not figures that merely
exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from deckscan.config import Settings
from deckscan.models import SEVERITY_RANK, DeckAnalysis, Severity

STRUCTURED_METHODS = {"table", "cell"}


def provenance_density(analysis: DeckAnalysis, settings: Settings) -> float:
    """Share of extracted headline metrics that came from a table or a cell."""
    present = 0
    structured = 0
    for name in settings.headline_metrics:
        metric = analysis.primary(name)
        if metric is None or metric.value is None or not metric.provenance:
            continue
        present += 1
        if any(p.method in STRUCTURED_METHODS for p in metric.provenance):
            structured += 1
    if present == 0:
        return 0.0
    return structured / present


def grounding_score(analysis: DeckAnalysis, settings: Settings) -> int:
    scoring = settings.scoring
    score = scoring.start
    for flag in analysis.flags:
        score += scoring.flag_penalty.get(flag.severity, 0)
    for gap in analysis.gaps:
        score += scoring.gap_penalty.get(gap.severity, scoring.gap_penalty.get("medium", -4))
    score += round(scoring.provenance_bonus_max * provenance_density(analysis, settings))
    return max(0, min(100, score))


@dataclass(frozen=True)
class GroundingItem:
    """One line of the Needs Grounding block: a claim, and what would settle it."""

    code: str
    severity: Severity
    claim: str
    substantiation: str
    load_bearing: bool


def grounding_items(analysis: DeckAnalysis, settings: Settings) -> list[GroundingItem]:
    """The merged, ranked list investors act on, capped by ``render.max_grounding_items``.

    Flags and gaps are merged deliberately: the headline item is usually the
    missing cash flow, which is a gap, not a flag.
    """
    items = [
        GroundingItem(
            code=flag.code,
            severity=flag.severity,
            claim=flag.title,
            substantiation=flag.ask,
            load_bearing=flag.load_bearing,
        )
        for flag in analysis.flags
    ] + [
        GroundingItem(
            code=gap.field.upper(),
            severity=gap.severity,
            claim=gap.label,
            substantiation=gap.ask,
            load_bearing=gap.load_bearing,
        )
        for gap in analysis.gaps
    ]

    items.sort(key=lambda item: (SEVERITY_RANK[item.severity], not item.load_bearing, item.code))

    # One line per distinct subject. A completeness flag and its gap describe the
    # same hole in the same words but ask for it slightly differently, so match on
    # the claim as well as the question - otherwise the missing cash flow burns two
    # of the six slots and pushes a real finding off the page.
    seen_claims: set[str] = set()
    seen_asks: set[str] = set()
    deduped: list[GroundingItem] = []
    for item in items:
        claim = item.claim.strip().lower()
        ask = item.substantiation.strip().lower()
        if claim in seen_claims or ask in seen_asks:
            continue
        seen_claims.add(claim)
        seen_asks.add(ask)
        deduped.append(item)
    return deduped[: settings.render.max_grounding_items]
