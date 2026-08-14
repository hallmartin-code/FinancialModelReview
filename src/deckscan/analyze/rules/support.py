"""Shared helpers for the rule modules.

Every rule builds its flag through :func:`make_flag`, so all user-visible text
comes from ``rules.yaml`` and every finding is formatted with the actual numbers
the rule fired on.

Unit convention, applied everywhere: growth is a year-over-year **multiple**,
``m_t = value_t / value_{t-1}``. 15% growth is ``1.15``, never ``0.15``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation
from itertools import pairwise
from typing import Any

from deckscan.config import Settings
from deckscan.formatting import (
    fmt_money,
    fmt_months,
    fmt_multiple,
    fmt_percent,
    fmt_plain,
)
from deckscan.models import DeckAnalysis, Flag, Gap, Metric, Provenance, Severity

__all__ = [
    "COST_SERIES",
    "REVENUE_SERIES",
    "Point",
    "Step",
    "evidence_for",
    "fmt_money",
    "fmt_months",
    "fmt_multiple",
    "fmt_percent",
    "fmt_plain",
    "has_metric",
    "is_load_bearing",
    "label_for",
    "make_flag",
    "make_gap",
    "metric_unit",
    "metric_value",
    "primary_metric_or_none",
    "safe_ratio",
    "steps",
    "track",
    "window_multiple",
]

#: Series the growth rules read, in preference order.
REVENUE_SERIES = ("revenue", "arr", "mrr")
#: Series the cost rules read, in preference order.
COST_SERIES = ("opex", "burn_monthly")


class _Lenient(dict[str, Any]):
    """Missing template keys render as '?' rather than crashing a run."""

    def __missing__(self, key: str) -> str:  # pragma: no cover - config typo guard
        return "?"


def safe_ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    """Ratio, or None when it is undefined or meaningless."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    try:
        return numerator / denominator
    except (DivisionByZero, InvalidOperation):  # pragma: no cover - guarded above
        return None


@dataclass(frozen=True)
class Point:
    """One period of a series, with the side of the actual/projected line it sits on."""

    period: str
    value: Decimal
    projected: bool


@dataclass(frozen=True)
class Step:
    """A period-over-period change, expressed as a multiple."""

    from_period: str
    to_period: str
    multiple: Decimal
    projected: bool
    """True when the destination period is projected."""


def track(analysis: DeckAnalysis, names: tuple[str, ...]) -> list[Point]:
    """The first available series among ``names``, ordered actual-then-projected."""
    for name in names:
        points = analysis.series.get(name)
        if points and len(points) >= 1:
            return [
                Point(period=period, value=value, projected=period in analysis.projected_periods)
                for period, value in points
            ]
    return []


def steps(points: list[Point]) -> list[Step]:
    """Period-over-period multiples across a track."""
    out: list[Step] = []
    for previous, current in pairwise(points):
        multiple = safe_ratio(current.value, previous.value)
        if multiple is None:
            continue
        out.append(
            Step(
                from_period=previous.period,
                to_period=current.period,
                multiple=multiple,
                projected=current.projected,
            )
        )
    return out


def window_multiple(points: list[Point]) -> Decimal | None:
    """Growth across the whole track: last value / first value."""
    if len(points) < 2:
        return None
    return safe_ratio(points[-1].value, points[0].value)


def evidence_for(
    analysis: DeckAnalysis,
    names: tuple[str, ...] | str,
    periods: tuple[str, ...] | None = None,
    limit: int = 3,
) -> list[Provenance]:
    """Provenance for the extracted instances behind a finding."""
    wanted = (names,) if isinstance(names, str) else names
    out: list[Provenance] = []
    for name in wanted:
        for metric in analysis.metrics.get(name, []):
            if periods is not None and metric.period not in periods:
                continue
            out.extend(metric.provenance)
    deduped: list[Provenance] = []
    for item in out:
        if item not in deduped:
            deduped.append(item)
    return deduped[:limit]


def metric_value(analysis: DeckAnalysis, name: str) -> Decimal | None:
    metric = analysis.primary(name)
    return metric.value if metric else None


def metric_unit(analysis: DeckAnalysis, name: str) -> str | None:
    metric = analysis.primary(name)
    return metric.unit if metric else None


def has_metric(analysis: DeckAnalysis, name: str) -> bool:
    return metric_value(analysis, name) is not None


def is_load_bearing(analysis: DeckAnalysis, evidence: list[Provenance]) -> bool:
    """True when the evidence touches the raise ask or the terminal-year revenue."""
    anchors: set[str] = set()
    if analysis.raise_ask:
        anchors.update(p.locator for p in analysis.raise_ask.provenance)
    revenue = track(analysis, REVENUE_SERIES)
    if revenue:
        terminal = revenue[-1].period
        for metric in analysis.metrics.get("revenue", []) + analysis.metrics.get("arr", []):
            if metric.period == terminal:
                anchors.update(p.locator for p in metric.provenance)
    return any(item.locator in anchors for item in evidence)


def make_flag(
    settings: Settings,
    analysis: DeckAnalysis,
    code: str,
    values: dict[str, Any],
    evidence: list[Provenance],
    severity: Severity | None = None,
) -> Flag | None:
    """Build one flag from its ``rules.yaml`` entry. Returns None when disabled."""
    text = settings.rule(code)
    if not text.enabled:
        return None
    lenient = _Lenient(values)
    return Flag(
        code=code,
        severity=severity or text.severity,
        title=text.title,
        finding=" ".join(text.finding.format_map(lenient).split()),
        why_it_matters=" ".join(text.why_it_matters.split()),
        ask=" ".join(text.ask.format_map(lenient).split()),
        evidence=evidence,
        load_bearing=is_load_bearing(analysis, evidence),
    )


def make_gap(settings: Settings, field: str, load_bearing: bool = False) -> Gap:
    """Build one gap from its ``rules.yaml`` entry."""
    text = settings.gap(field)
    return Gap(
        field=field,
        label=text.label,
        severity=text.severity,
        ask=" ".join(text.ask.split()),
        load_bearing=load_bearing,
    )


def primary_metric_or_none(analysis: DeckAnalysis, name: str) -> Metric | None:
    return analysis.primary(name)


#: Canonical names that read as acronyms rather than words.
_ACRONYMS = {
    "arr",
    "mrr",
    "cac",
    "ltv",
    "tam",
    "sam",
    "som",
    "arpu",
    "cogs",
    "opex",
    "ltv_cac_ratio",
}


def label_for(name: str) -> str:
    """Human label for a canonical metric name, for use inside findings."""
    if name == "ltv_cac_ratio":
        return "LTV:CAC"
    if name in _ACRONYMS:
        return name.upper()
    return name.replace("_", " ").capitalize()
