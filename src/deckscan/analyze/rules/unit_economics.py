"""Unit economics rules.

LTV is the figure most often inflated and least often derived, so these rules
care as much about whether the inputs were disclosed as about the ratio itself.
"""

from __future__ import annotations

from decimal import Decimal

from deckscan.analyze.rules.support import (
    REVENUE_SERIES,
    evidence_for,
    fmt_money,
    fmt_months,
    fmt_multiple,
    fmt_percent,
    make_flag,
    make_gap,
    metric_value,
    safe_ratio,
    track,
    window_multiple,
)
from deckscan.config import Settings
from deckscan.models import DeckAnalysis, RuleResult

SECTOR_UNKNOWN = "an undetermined sector"


def sector_kind(analysis: DeckAnalysis, settings: Settings) -> str:
    """'b2b', 'b2c', or '' when the materials do not say."""
    haystack = " ".join(
        part.lower()
        for part in (analysis.sector or "", *(analysis.narrative or {}).values())
        if part
    )
    if not haystack:
        return ""
    for word in settings.keywords.b2b_keywords:
        if word.lower() in haystack:
            return "b2b"
    for word in settings.keywords.b2c_keywords:
        if word.lower() in haystack:
            return "b2c"
    return ""


def payback_ceiling(analysis: DeckAnalysis, settings: Settings) -> tuple[Decimal, str, bool]:
    """(ceiling in months, label for the finding, whether the default was assumed)."""
    ceilings = settings.thresholds.unit_economics.max_cac_payback_months
    kind = sector_kind(analysis, settings)
    if kind and kind in ceilings:
        return Decimal(str(ceilings[kind])), kind.upper(), False
    return Decimal(str(ceilings["default"])), SECTOR_UNKNOWN, True


def _ltv_cac_ceiling(analysis: DeckAnalysis, settings: Settings) -> tuple[Decimal, str]:
    thresholds = settings.thresholds.unit_economics
    stage = (analysis.stage_guess or "").strip().lower().replace("-", "_").replace(" ", "_")
    by_stage = thresholds.ltv_cac_ceiling_by_stage
    if stage in by_stage:
        return Decimal(str(by_stage[stage])), stage.replace("_", "-")
    return Decimal(str(thresholds.ltv_cac_ceiling)), "an unstated stage"


def evaluate(analysis: DeckAnalysis, settings: Settings) -> RuleResult:
    result = RuleResult()
    thresholds = settings.thresholds.unit_economics

    ltv = metric_value(analysis, "ltv")
    cac = metric_value(analysis, "cac")
    stated_ratio = metric_value(analysis, "ltv_cac_ratio")
    gross_margin = metric_value(analysis, "gross_margin")
    churn = metric_value(analysis, "churn_rate")
    retention = metric_value(analysis, "retention_rate")
    arpu = metric_value(analysis, "arpu")
    lifetime = metric_value(analysis, "customer_lifetime_months")
    payback = metric_value(analysis, "cac_payback_months")

    ratio = stated_ratio if stated_ratio is not None else safe_ratio(ltv, cac)

    # --- LTV:CAC -------------------------------------------------------------
    if ratio is not None:
        ceiling, stage_label = _ltv_cac_ceiling(analysis, settings)
        evidence = evidence_for(analysis, ("ltv", "cac", "ltv_cac_ratio"))
        if ratio < Decimal(str(thresholds.ltv_cac_underwater)):
            flag = make_flag(
                settings,
                analysis,
                "LTV_CAC_UNDERWATER",
                {
                    "ltv": fmt_money(ltv),
                    "cac": fmt_money(cac),
                    "ratio": fmt_multiple(ratio),
                },
                evidence,
            )
            if flag:
                result.flags.append(flag)
        elif ratio > ceiling:
            severity = None
            if ratio > Decimal(str(thresholds.ltv_cac_critical)):
                severity = settings.rule("LTV_CAC_UNREALISTIC").critical_severity
            flag = make_flag(
                settings,
                analysis,
                "LTV_CAC_UNREALISTIC",
                {
                    "ltv": fmt_money(ltv),
                    "cac": fmt_money(cac),
                    "ratio": fmt_multiple(ratio),
                    "ceiling": fmt_multiple(ceiling),
                    "stage_label": stage_label,
                },
                evidence,
                severity=severity,
            )
            if flag:
                result.flags.append(flag)
    else:
        if ltv is None:
            result.gaps.append(make_gap(settings, "ltv"))
        if cac is None:
            result.gaps.append(make_gap(settings, "cac"))

    # --- LTV without a derivation -------------------------------------------
    if ltv is not None:
        missing = [
            label
            for label, value in (
                ("gross margin", gross_margin),
                ("churn or retention rate", churn if churn is not None else retention),
                ("ARPU", arpu),
            )
            if value is None
        ]
        if missing:
            flag = make_flag(
                settings,
                analysis,
                "LTV_METHOD_UNDISCLOSED",
                {"ltv": fmt_money(ltv), "missing_inputs": ", ".join(missing)},
                evidence_for(analysis, "ltv"),
            )
            if flag:
                result.flags.append(flag)

    # --- LTV computed on revenue rather than gross profit --------------------
    if ltv is not None and arpu is not None and lifetime is not None and gross_margin is not None:
        revenue_ltv = arpu * lifetime
        tolerance = Decimal(str(thresholds.ltv_revenue_match_tolerance))
        margin_ceiling = Decimal(str(thresholds.ltv_margin_check_gross_margin_max))
        if ltv != 0:
            closeness = abs(ltv - revenue_ltv) / abs(ltv)
            if closeness <= tolerance and gross_margin < margin_ceiling:
                flag = make_flag(
                    settings,
                    analysis,
                    "LTV_IGNORES_MARGIN",
                    {
                        "ltv": fmt_money(ltv),
                        "arpu": fmt_money(arpu),
                        "lifetime_months": fmt_months(lifetime),
                        "tolerance": fmt_percent(tolerance),
                        "gross_margin": fmt_percent(gross_margin),
                        "adjusted_ltv": fmt_money(revenue_ltv * gross_margin),
                    },
                    evidence_for(analysis, ("ltv", "arpu", "gross_margin")),
                )
                if flag:
                    result.flags.append(flag)

    # --- CAC payback ---------------------------------------------------------
    if payback is not None:
        ceiling, label, _assumed = payback_ceiling(analysis, settings)
        if payback > ceiling:
            flag = make_flag(
                settings,
                analysis,
                "CAC_PAYBACK_LONG",
                {
                    "payback_months": fmt_months(payback),
                    "ceiling": fmt_months(ceiling),
                    "sector_label": label,
                },
                evidence_for(analysis, "cac_payback_months"),
            )
            if flag:
                result.flags.append(flag)
    elif cac is not None:
        result.gaps.append(make_gap(settings, "cac_payback"))

    # --- churn that implies an unbounded lifetime ----------------------------
    for name, value, label, unit in (
        ("churn_rate", churn, "Churn", "%"),
        ("retention_rate", retention, "Retention", "%"),
    ):
        if value is None:
            continue
        infinite = value <= 0 if name == "churn_rate" else value >= 1
        if infinite and ltv is not None:
            flag = make_flag(
                settings,
                analysis,
                "CHURN_IMPLIES_INFINITE_LIFETIME",
                {
                    "metric_label": label,
                    "value": fmt_percent(value),
                    "unit": unit,
                },
                evidence_for(analysis, name),
            )
            if flag:
                result.flags.append(flag)
    if churn is None and retention is None:
        result.gaps.append(make_gap(settings, "churn_or_retention"))
    if gross_margin is None:
        result.gaps.append(make_gap(settings, "gross_margin"))
    if arpu is None:
        result.gaps.append(make_gap(settings, "arpu"))

    # --- CAC held flat while volume scales -----------------------------------
    cac_points = track(analysis, ("cac",))
    volume_points = track(analysis, ("customers",))
    if len(cac_points) >= 2 and len(volume_points) >= 2:
        cac_multiple = window_multiple(cac_points)
        volume_multiple = window_multiple(volume_points)
        tolerance = Decimal(str(thresholds.cac_static_tolerance))
        if (
            cac_multiple is not None
            and volume_multiple is not None
            and cac_multiple <= Decimal(1) + tolerance
            and volume_multiple > Decimal(1)
        ):
            flag = make_flag(
                settings,
                analysis,
                "CAC_STATIC_ACROSS_PLAN",
                {
                    "cac_start": fmt_money(cac_points[0].value),
                    "cac_end": fmt_money(cac_points[-1].value),
                    "start_period": cac_points[0].period,
                    "end_period": cac_points[-1].period,
                    "volume_multiple": fmt_multiple(volume_multiple),
                },
                evidence_for(analysis, ("cac", "customers")),
            )
            if flag:
                result.flags.append(flag)

    # --- revenue scaling without go-to-market spend --------------------------
    revenue_points = track(analysis, REVENUE_SERIES)
    sm_points = track(analysis, ("sales_marketing_spend",))
    if len(revenue_points) >= 2:
        if len(sm_points) >= 2:
            revenue_multiple = window_multiple(revenue_points)
            sm_multiple = window_multiple(sm_points)
            if (
                revenue_multiple is not None
                and sm_multiple is not None
                and revenue_multiple > Decimal(str(thresholds.organic_growth_revenue_multiple))
                and sm_multiple < Decimal(str(thresholds.organic_growth_sm_multiple))
            ):
                flag = make_flag(
                    settings,
                    analysis,
                    "ORGANIC_GROWTH_ASSUMED",
                    {
                        "revenue_multiple": fmt_multiple(revenue_multiple),
                        "sm_multiple": fmt_multiple(sm_multiple),
                        "start_period": revenue_points[0].period,
                        "end_period": revenue_points[-1].period,
                    },
                    evidence_for(analysis, (*REVENUE_SERIES, "sales_marketing_spend")),
                )
                if flag:
                    result.flags.append(flag)
        else:
            result.gaps.append(make_gap(settings, "sales_marketing_spend"))

    return result
