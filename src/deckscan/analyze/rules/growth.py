"""Growth realism rules.

The diagnostic question behind all of them: does the plan's growth rate step up
exactly where the evidence stops?
"""

from __future__ import annotations

from decimal import Decimal

from deckscan.analyze.rules.support import (
    REVENUE_SERIES,
    Point,
    Step,
    evidence_for,
    fmt_money,
    fmt_multiple,
    fmt_percent,
    make_flag,
    make_gap,
    metric_unit,
    metric_value,
    safe_ratio,
    steps,
    track,
)
from deckscan.config import Settings
from deckscan.models import DeckAnalysis, Provenance, RuleResult


def _cagr(base: Decimal, terminal: Decimal, years: int) -> Decimal | None:
    if years <= 0 or base <= 0 or terminal <= 0:
        return None
    return Decimal(str((float(terminal) / float(base)) ** (1 / years) - 1))


def _last_actual_step(all_steps: list[Step]) -> Step | None:
    actual = [s for s in all_steps if not s.projected]
    return actual[-1] if actual else None


def _first_projected_step(all_steps: list[Step]) -> Step | None:
    projected = [s for s in all_steps if s.projected]
    return projected[0] if projected else None


def evaluate(analysis: DeckAnalysis, settings: Settings) -> RuleResult:
    result = RuleResult()
    thresholds = settings.thresholds.growth
    points = track(analysis, REVENUE_SERIES)

    if len(points) < 2:
        result.gaps.append(make_gap(settings, "revenue_series", load_bearing=True))
        return result

    actuals = [p for p in points if not p.projected]
    projections = [p for p in points if p.projected]
    all_steps = steps(points)
    projected_steps = [s for s in all_steps if s.projected]
    evidence = evidence_for(analysis, REVENUE_SERIES, limit=3)

    if not projections:
        result.gaps.append(make_gap(settings, "projected_periods"))
        return result

    if not actuals:
        flag = make_flag(settings, analysis, "NO_ACTUALS", {}, [])
        if flag:
            result.flags.append(flag)
        result.gaps.append(make_gap(settings, "actual_periods", load_bearing=True))
    else:
        _hockey_stick(analysis, settings, points, all_steps, projected_steps, evidence, result)
        _discontinuity(analysis, settings, all_steps, evidence, result)
        _flat_then_spike(analysis, settings, all_steps, projected_steps, evidence, result)

    _terminal_year(analysis, settings, points, result)
    del thresholds
    return result


def _hockey_stick(
    analysis: DeckAnalysis,
    settings: Settings,
    points: list[Point],
    all_steps: list[Step],
    projected_steps: list[Step],
    evidence: list[Provenance],
    result: RuleResult,
) -> None:
    thresholds = settings.thresholds.growth
    if not projected_steps:
        return
    trailing = _last_actual_step(all_steps)
    if trailing is None or trailing.multiple <= 0:
        return

    peak = max(projected_steps, key=lambda s: s.multiple)
    ratio = safe_ratio(peak.multiple, trailing.multiple)
    if ratio is None:
        return
    if not (
        peak.multiple >= Decimal(str(thresholds.hockey_stick_multiple))
        and ratio >= Decimal(str(thresholds.hockey_stick_ratio_vs_actual))
    ):
        return

    base = points[0]
    years = len(points) - 1
    cagr = _cagr(base.value, points[-1].value, years)

    # Critical when a projected year implies a very large multiple off a small base.
    severity = None
    base_ceiling = Decimal(str(thresholds.hockey_stick_critical_base_usd))
    critical_multiple = Decimal(str(thresholds.hockey_stick_critical_multiple))
    for step in projected_steps:
        prior = next((p for p in points if p.period == step.from_period), None)
        if prior is not None and step.multiple >= critical_multiple and prior.value < base_ceiling:
            severity = settings.rule("HOCKEY_STICK_REVENUE").critical_severity
            break

    flag = make_flag(
        settings,
        analysis,
        "HOCKEY_STICK_REVENUE",
        {
            "peak_multiple": fmt_multiple(peak.multiple),
            "peak_period": peak.to_period,
            "implied_cagr": fmt_percent(cagr) if cagr is not None else "?",
            "actual_multiple": fmt_multiple(trailing.multiple),
        },
        evidence,
        severity=severity,
    )
    if flag:
        result.flags.append(flag)


def _discontinuity(
    analysis: DeckAnalysis,
    settings: Settings,
    all_steps: list[Step],
    evidence: list[Provenance],
    result: RuleResult,
) -> None:
    thresholds = settings.thresholds.growth
    trailing = _last_actual_step(all_steps)
    first = _first_projected_step(all_steps)
    if trailing is None or first is None:
        return
    ratio = safe_ratio(first.multiple, trailing.multiple)
    if ratio is None or ratio < Decimal(str(thresholds.growth_discontinuity_ratio)):
        return
    flag = make_flag(
        settings,
        analysis,
        "GROWTH_DISCONTINUITY",
        {
            "last_actual_multiple": fmt_multiple(trailing.multiple),
            "last_actual_period": trailing.to_period,
            "first_projected_multiple": fmt_multiple(first.multiple),
            "first_projected_period": first.to_period,
            "ratio": fmt_multiple(ratio),
        },
        evidence,
    )
    if flag:
        result.flags.append(flag)


def _flat_then_spike(
    analysis: DeckAnalysis,
    settings: Settings,
    all_steps: list[Step],
    projected_steps: list[Step],
    evidence: list[Provenance],
    result: RuleResult,
) -> None:
    thresholds = settings.thresholds.growth
    trailing = _last_actual_step(all_steps)
    if trailing is None or trailing.multiple >= Decimal(str(thresholds.flat_actual_multiple_max)):
        return
    spikes = [
        s for s in projected_steps if s.multiple > Decimal(str(thresholds.flat_then_spike_multiple))
    ]
    if not spikes:
        return
    spike = max(spikes, key=lambda s: s.multiple)
    flag = make_flag(
        settings,
        analysis,
        "FLAT_THEN_SPIKE",
        {
            "last_actual_multiple": fmt_multiple(trailing.multiple),
            "last_actual_period": trailing.to_period,
            "spike_multiple": fmt_multiple(spike.multiple),
            "spike_period": spike.to_period,
        },
        evidence,
    )
    if flag:
        result.flags.append(flag)


def _terminal_year(
    analysis: DeckAnalysis,
    settings: Settings,
    points: list[Point],
    result: RuleResult,
) -> None:
    thresholds = settings.thresholds.growth
    projections = [p for p in points if p.projected]
    if not projections:
        return
    terminal = projections[-1]

    tam = metric_value(analysis, "tam")
    unit = metric_unit(analysis, "tam")
    # A TAM in the wrong unit, or none at all, is a gap - never a guessed denominator.
    if tam is None or tam <= 0 or (unit and unit not in {"USD", "EUR", "GBP", "$"}):
        result.gaps.append(make_gap(settings, "tam"))
        return

    share = safe_ratio(terminal.value, tam)
    ceiling = Decimal(str(thresholds.market_share_ceiling))
    if share is None or share <= ceiling:
        return

    evidence = evidence_for(
        analysis, ("revenue", "arr"), periods=(terminal.period,)
    ) or evidence_for(analysis, REVENUE_SERIES)
    evidence = evidence + evidence_for(analysis, "tam", limit=1)
    flag = make_flag(
        settings,
        analysis,
        "UNSUPPORTED_TERMINAL_YEAR",
        {
            "terminal_revenue": fmt_money(terminal.value),
            "terminal_period": terminal.period,
            "implied_share": fmt_percent(share),
            "tam": fmt_money(tam),
            "ceiling_share": fmt_percent(ceiling),
        },
        evidence[:3],
    )
    if flag:
        result.flags.append(flag)
