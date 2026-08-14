"""Completeness of the financial picture.

Every check here emits a ``Gap``. It additionally emits a ``Flag`` when the
materials claim financial rigor — a projected revenue series was extracted, or a
financials section was found — because a deck that presents itself as having
financials and then omits the cash flow is making a different statement than a
narrative deck that never claimed to.

All codes in this family are evidence-exempt: they assert an absence, so they
carry no locator and no number.
"""

from __future__ import annotations

from deckscan.analyze.rules.support import REVENUE_SERIES, make_flag, make_gap, track
from deckscan.config import Settings
from deckscan.models import DeckAnalysis, RuleResult

#: claim field -> (gap field, flag code)
CHECKS: tuple[tuple[str, str, str], ...] = (
    ("has_cash_flow", "cash_flow", "MISSING_CASH_FLOW"),
    ("has_income_statement_detail", "income_statement_detail", "MISSING_INCOME_STATEMENT_DETAIL"),
    ("has_assumptions", "assumptions", "MISSING_ASSUMPTIONS_PAGE"),
    ("has_sensitivity_case", "sensitivity_case", "NO_SENSITIVITY_CASE"),
    ("has_cap_table", "cap_table", "MISSING_CAP_TABLE_OR_PRIOR_RAISES"),
    ("has_use_of_funds", "use_of_funds", "MISSING_USE_OF_FUNDS"),
    ("has_pricing", "pricing", "MISSING_PRICING"),
    ("has_tam_methodology", "tam_methodology", "MISSING_TAM_METHODOLOGY"),
)

#: Stages at which a missing balance sheet is a flag rather than only a gap.
LATE_STAGES = {"series_b", "series_c", "growth"}


def claims_financial_rigor(analysis: DeckAnalysis, settings: Settings) -> bool:
    """Whether the materials present themselves as containing financial substance."""
    if analysis.claims.get("claims_financial_rigor"):
        return True
    points = track(analysis, REVENUE_SERIES)
    if any(point.projected for point in points):
        return True
    haystack = " ".join(v.lower() for v in (analysis.narrative or {}).values() if v)
    return any(word.lower() in haystack for word in settings.keywords.financials_section_keywords)


def evaluate(analysis: DeckAnalysis, settings: Settings) -> RuleResult:
    result = RuleResult()
    rigor = claims_financial_rigor(analysis, settings)

    for claim, gap_field, code in CHECKS:
        if analysis.claims.get(claim):
            continue
        result.gaps.append(make_gap(settings, gap_field, load_bearing=gap_field == "cash_flow"))
        if rigor:
            flag = make_flag(settings, analysis, code, {}, [])
            if flag:
                result.flags.append(flag)

    # A balance sheet only becomes a flag where one is actually expected.
    if not analysis.claims.get("has_balance_sheet"):
        result.gaps.append(make_gap(settings, "balance_sheet"))
        stage = (analysis.stage_guess or "").replace("-", "_").replace(" ", "_")
        if rigor and stage in LATE_STAGES:
            flag = make_flag(settings, analysis, "MISSING_BALANCE_SHEET", {}, [])
            if flag:
                result.flags.append(flag)

    # Projection horizon.
    minimum = settings.thresholds.completeness.min_projected_years
    if analysis.projected_periods and len(analysis.projected_periods) < minimum:
        result.gaps.append(make_gap(settings, "projection_horizon"))
        if rigor:
            flag = make_flag(settings, analysis, "PROJECTION_HORIZON_SHORT", {}, [])
            if flag:
                result.flags.append(flag)

    return result
