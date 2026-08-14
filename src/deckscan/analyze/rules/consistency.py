"""Internal consistency rules.

These read ``metrics[name]`` as the list it is, comparing every extracted
instance of a figure against every other — deck against model, and slide against
slide. A figure that appears twice with two values is the cheapest thing for
diligence to find and the most expensive thing to explain later.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from deckscan.analyze.rules.support import (
    REVENUE_SERIES,
    evidence_for,
    fmt_money,
    fmt_multiple,
    fmt_percent,
    fmt_plain,
    label_for,
    make_flag,
    make_gap,
    metric_unit,
    metric_value,
    safe_ratio,
    track,
)
from deckscan.config import Settings
from deckscan.models import DeckAnalysis, Metric, RuleResult

_YEAR = re.compile(r"(19|20)\d{2}")
_QUARTER = re.compile(r"q([1-4])", re.IGNORECASE)
_ISO = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")

_MONEY_UNITS = {"USD", "EUR", "GBP", "$"}


def _format_value(value: Decimal, unit: str | None) -> str:
    if unit in _MONEY_UNITS:
        return fmt_money(value, unit)
    if unit == "%":
        return f"{fmt_percent(value)}%"
    if unit == "x":
        return f"{fmt_multiple(value)}x"
    return fmt_plain(value)


def _period_end(label: str) -> date | None:
    """Approximate end date of a period label. None when it cannot be read."""
    text = label.strip()
    iso = _ISO.match(text)
    if iso:
        year, month = int(iso.group(1)), int(iso.group(2))
        return date(year, month, 28)
    year_match = _YEAR.search(text)
    if not year_match:
        return None
    year = int(year_match.group(0))
    quarter = _QUARTER.search(text)
    if quarter:
        month = int(quarter.group(1)) * 3
        return date(year, month, 28)
    return date(year, 12, 31)


def _deck_date(analysis: DeckAnalysis) -> date | None:
    raw = (analysis.deck_date or "").strip()
    if not raw:
        return None
    iso = _ISO.match(raw)
    if iso:
        year, month = int(iso.group(1)), int(iso.group(2))
        day = int(iso.group(3) or 28)
        try:
            return date(year, month, min(day, 28))
        except ValueError:  # pragma: no cover - defensive
            return None
    year_match = _YEAR.search(raw)
    return date(int(year_match.group(0)), 12, 31) if year_match else None


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def evaluate(analysis: DeckAnalysis, settings: Settings) -> RuleResult:
    result = RuleResult()
    thresholds = settings.thresholds.consistency

    _inconsistencies(analysis, settings, result, Decimal(str(thresholds.value_tolerance)))
    _arithmetic(analysis, settings, result, Decimal(str(thresholds.arithmetic_tolerance)))
    _unit_ambiguity(analysis, settings, result)
    _stale(analysis, settings, result, thresholds.stale_data_months)
    return result


def _groups(analysis: DeckAnalysis) -> dict[tuple[str, str | None], list[Metric]]:
    grouped: dict[tuple[str, str | None], list[Metric]] = {}
    for name, values in analysis.metrics.items():
        for metric in values:
            grouped.setdefault((name, metric.period), []).append(metric)
    return grouped


def _inconsistencies(
    analysis: DeckAnalysis,
    settings: Settings,
    result: RuleResult,
    tolerance: Decimal,
) -> None:
    for (name, period), values in sorted(
        _groups(analysis).items(), key=lambda kv: (kv[0][0], kv[0][1] or "")
    ):
        numbers = [m for m in values if m.value is not None]
        if len(numbers) < 2:
            continue
        low = min(numbers, key=lambda m: m.value or Decimal(0))
        high = max(numbers, key=lambda m: m.value or Decimal(0))
        if low.value is None or high.value is None or high.value == 0:
            continue
        spread = safe_ratio(abs(high.value - low.value), abs(high.value))
        if spread is None or spread <= tolerance:
            continue
        label = label_for(name) + (f" ({period})" if period else "")
        evidence = [*low.provenance, *high.provenance][:3]
        flag = make_flag(
            settings,
            analysis,
            "DATA_INCONSISTENCY",
            {
                "metric_label": label,
                "value_a": _format_value(low.value, low.unit),
                "locator_a": low.provenance[0].locator if low.provenance else "unknown",
                "value_b": _format_value(high.value, high.unit),
                "locator_b": high.provenance[0].locator if high.provenance else "unknown",
                "delta": fmt_percent(spread),
            },
            evidence,
        )
        if flag:
            result.flags.append(flag)


def _arithmetic(
    analysis: DeckAnalysis,
    settings: Settings,
    result: RuleResult,
    tolerance: Decimal,
) -> None:
    checks: list[tuple[str, Decimal, Decimal, str | None]] = []

    # Gross margin must equal (revenue - COGS) / revenue for the same period.
    for period in analysis.actual_periods + analysis.projected_periods:
        revenue = analysis.value_at("revenue", period)
        cogs = analysis.value_at("cogs", period)
        stated = next(
            (m.value for m in analysis.metrics.get("gross_margin", []) if m.period == period),
            None,
        )
        if revenue and cogs is not None and stated is not None and revenue != 0:
            computed = (revenue - cogs) / revenue
            checks.append((f"Gross margin ({period})", stated, computed, "%"))

    # A stated LTV:CAC ratio must equal LTV / CAC.
    stated_ratio = metric_value(analysis, "ltv_cac_ratio")
    computed_ratio = safe_ratio(metric_value(analysis, "ltv"), metric_value(analysis, "cac"))
    if stated_ratio is not None and computed_ratio is not None:
        checks.append(("The stated LTV:CAC ratio", stated_ratio, computed_ratio, "x"))

    for label, stated, computed, unit in checks:
        if computed == 0:
            continue
        delta = abs(stated - computed) / abs(computed)
        if delta <= tolerance:
            continue
        flag = make_flag(
            settings,
            analysis,
            "ARITHMETIC_ERROR",
            {
                "label": label,
                "stated": _format_value(stated, unit),
                "computed": _format_value(computed, unit),
                "delta": fmt_percent(delta),
            },
            evidence_for(analysis, ("gross_margin", "revenue", "cogs", "ltv_cac_ratio")),
        )
        if flag:
            result.flags.append(flag)


def _unit_ambiguity(analysis: DeckAnalysis, settings: Settings, result: RuleResult) -> None:
    ceiling = Decimal(str(settings.normalization.bare_number_ambiguity_ceiling))
    points = track(analysis, REVENUE_SERIES)
    unit = metric_unit(analysis, "revenue") or metric_unit(analysis, "arr")
    suspicious = [p for p in points if 0 < p.value < ceiling]
    if len(suspicious) < 2 or unit not in _MONEY_UNITS:
        return
    examples = ", ".join(f"{p.period}: {fmt_plain(p.value)}" for p in suspicious[:3])
    flag = make_flag(
        settings,
        analysis,
        "UNIT_AMBIGUITY",
        {"label": "The revenue series", "examples": examples},
        evidence_for(analysis, REVENUE_SERIES),
    )
    if flag:
        result.flags.append(flag)


def _stale(
    analysis: DeckAnalysis,
    settings: Settings,
    result: RuleResult,
    max_months: int,
) -> None:
    if not analysis.actual_periods:
        return
    as_of = _deck_date(analysis)
    if as_of is None:
        # Never fall back to today's date: it would make the same input produce a
        # different report tomorrow.
        result.gaps.append(make_gap(settings, "deck_date"))
        return

    last_label = analysis.actual_periods[-1]
    last_end = _period_end(last_label)
    if last_end is None:
        return
    months = _months_between(last_end, as_of)
    if months <= max_months:
        return
    flag = make_flag(
        settings,
        analysis,
        "STALE_DATA",
        {
            "last_actual_period": last_label,
            "months_stale": str(months),
            "deck_date": analysis.deck_date or "unknown",
        },
        evidence_for(analysis, REVENUE_SERIES, periods=(last_label,))
        or evidence_for(analysis, REVENUE_SERIES),
    )
    if flag:
        result.flags.append(flag)
