"""Burn, runway, and cost realism rules.

The recurring tell: a plan whose revenue scales but whose cost base does not.
"""

from __future__ import annotations

from decimal import Decimal

from deckscan.analyze.rules.support import (
    COST_SERIES,
    REVENUE_SERIES,
    Point,
    evidence_for,
    fmt_money,
    fmt_months,
    fmt_multiple,
    fmt_percent,
    fmt_plain,
    has_metric,
    make_flag,
    make_gap,
    metric_value,
    safe_ratio,
    track,
    window_multiple,
)
from deckscan.config import Settings
from deckscan.models import DeckAnalysis, RuleResult


def _common_window(left: list[Point], right: list[Point]) -> tuple[list[Point], list[Point]]:
    """The two tracks restricted to the periods they share, in order."""
    shared = [p.period for p in left if any(q.period == p.period for q in right)]
    if len(shared) < 2:
        return [], []
    return (
        [p for p in left if p.period in shared],
        [p for p in right if p.period in shared],
    )


def evaluate(analysis: DeckAnalysis, settings: Settings) -> RuleResult:
    result = RuleResult()
    thresholds = settings.thresholds.burn

    burn = metric_value(analysis, "burn_monthly")
    headcount = metric_value(analysis, "headcount")
    runway = metric_value(analysis, "runway_months")
    raise_amount = metric_value(analysis, "raise_ask")

    cost_points = track(analysis, COST_SERIES)
    revenue_points = track(analysis, REVENUE_SERIES)
    headcount_points = track(analysis, ("headcount",))

    # --- implied cost per head ----------------------------------------------
    if burn is not None and headcount is not None and headcount > 0:
        per_head = safe_ratio(burn, headcount)
        floor = Decimal(str(thresholds.min_monthly_cost_per_head))
        if per_head is not None and per_head < floor:
            flag = make_flag(
                settings,
                analysis,
                "IMPLAUSIBLY_LOW_BURN",
                {
                    "burn": fmt_money(burn),
                    "headcount": fmt_plain(headcount),
                    "cost_per_head": fmt_money(per_head),
                    "floor": fmt_money(floor),
                },
                evidence_for(analysis, ("burn_monthly", "headcount")),
            )
            if flag:
                result.flags.append(flag)
    else:
        if burn is None:
            result.gaps.append(make_gap(settings, "burn_monthly"))
        if headcount is None and not headcount_points:
            result.gaps.append(make_gap(settings, "headcount"))

    # --- headcount grows faster than cost ------------------------------------
    if headcount_points and cost_points:
        heads, costs = _common_window(headcount_points, cost_points)
        head_multiple = window_multiple(heads)
        cost_multiple = window_multiple(costs)
        if (
            head_multiple is not None
            and cost_multiple is not None
            and head_multiple >= Decimal(str(thresholds.headcount_growth_floor))
            and cost_multiple < Decimal(str(thresholds.opex_growth_floor))
        ):
            flag = make_flag(
                settings,
                analysis,
                "OPEX_LAGS_HEADCOUNT",
                {
                    "headcount_multiple": fmt_multiple(head_multiple),
                    "opex_multiple": fmt_multiple(cost_multiple),
                    "start_period": heads[0].period,
                    "end_period": heads[-1].period,
                },
                evidence_for(analysis, ("headcount", "opex", "burn_monthly")),
            )
            if flag:
                result.flags.append(flag)

    # --- costs do not follow the revenue plan --------------------------------
    if revenue_points and cost_points:
        revenues, costs = _common_window(revenue_points, cost_points)
        revenue_multiple = window_multiple(revenues)
        cost_multiple = window_multiple(costs)
        if (
            revenue_multiple is not None
            and cost_multiple is not None
            and revenue_multiple > Decimal(str(thresholds.burn_flat_revenue_multiple))
            and cost_multiple < Decimal(str(thresholds.burn_flat_opex_multiple))
        ):
            flag = make_flag(
                settings,
                analysis,
                "BURN_FLAT_WHILE_REVENUE_SCALES",
                {
                    "revenue_multiple": fmt_multiple(revenue_multiple),
                    "opex_multiple": fmt_multiple(cost_multiple),
                    "start_period": revenues[0].period,
                    "end_period": revenues[-1].period,
                },
                evidence_for(analysis, REVENUE_SERIES + COST_SERIES),
            )
            if flag:
                result.flags.append(flag)
    elif revenue_points and not cost_points:
        result.gaps.append(make_gap(settings, "opex_series"))

    # --- runway --------------------------------------------------------------
    computed = safe_ratio(raise_amount, burn)
    if computed is not None:
        if runway is not None and runway > 0:
            delta = abs(computed - runway) / runway
            if delta > Decimal(str(thresholds.runway_mismatch_tolerance)):
                flag = make_flag(
                    settings,
                    analysis,
                    "RUNWAY_MISMATCH",
                    {
                        "raise": fmt_money(raise_amount),
                        "burn": fmt_money(burn),
                        "computed_runway": fmt_months(computed),
                        "stated_runway": fmt_months(runway),
                        "delta": fmt_percent(delta),
                    },
                    evidence_for(analysis, ("raise_ask", "burn_monthly", "runway_months")),
                )
                if flag:
                    result.flags.append(flag)
        if computed < Decimal(str(thresholds.min_runway_months)):
            flag = make_flag(
                settings,
                analysis,
                "RUNWAY_TOO_SHORT",
                {
                    "computed_runway": fmt_months(computed),
                    "floor": fmt_months(Decimal(str(thresholds.min_runway_months))),
                },
                evidence_for(analysis, ("raise_ask", "burn_monthly")),
            )
            if flag:
                result.flags.append(flag)
    else:
        if raise_amount is None:
            result.gaps.append(make_gap(settings, "raise_ask", load_bearing=True))
        if runway is None:
            result.gaps.append(make_gap(settings, "runway_months"))

    # --- burn without a headcount plan ---------------------------------------
    discusses_cost = burn is not None or bool(cost_points)
    knows_headcount = has_metric(analysis, "headcount") or bool(headcount_points)
    if discusses_cost and not knows_headcount and not analysis.claims.get("has_headcount_plan"):
        flag = make_flag(settings, analysis, "NO_HEADCOUNT_PLAN", {}, [])
        if flag:
            result.flags.append(flag)

    return result
