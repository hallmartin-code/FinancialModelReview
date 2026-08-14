"""Rule engine behaviour, driven through the real merge layer."""

from __future__ import annotations

import re

from conftest import analyze, codes, gap_fields, make_payload
from deckscan.models import evidence_exempt_codes

HOCKEY_STICK = make_payload(
    periods=[
        ("FY2024", "actual"),
        ("FY2025", "actual"),
        ("FY2026", "projected"),
        ("FY2027", "projected"),
    ],
    series=[
        ("revenue", "FY2024", "1000000"),
        ("revenue", "FY2025", "1150000"),  # 1.15x — 15% actual growth
        ("revenue", "FY2026", "9200000"),  # 8x
        ("revenue", "FY2027", "110400000"),  # 12x
    ],
)


def test_hockey_stick_family_fires_together(settings, fields):
    analysis = analyze(settings, fields, HOCKEY_STICK)
    fired = codes(analysis)
    assert "HOCKEY_STICK_REVENUE" in fired
    assert "GROWTH_DISCONTINUITY" in fired
    assert "FLAT_THEN_SPIKE" in fired


def test_every_growth_finding_quotes_its_numbers(settings, fields):
    analysis = analyze(settings, fields, HOCKEY_STICK)
    for flag in analysis.flags:
        if flag.code in evidence_exempt_codes():
            continue
        assert re.search(r"\d", flag.finding), flag.code
        assert flag.evidence, flag.code


def test_discontinuity_reports_the_elbow(settings, fields):
    analysis = analyze(settings, fields, HOCKEY_STICK)
    flag = next(f for f in analysis.flags if f.code == "GROWTH_DISCONTINUITY")
    assert "FY2025" in flag.finding and "FY2026" in flag.finding
    assert "1.2x" in flag.finding or "1.1x" in flag.finding  # trailing actual multiple


def test_no_actuals_fires_on_a_projection_only_deck(settings, fields):
    payload = make_payload(
        periods=[("FY2026", "projected"), ("FY2027", "projected")],
        series=[("revenue", "FY2026", "1000000"), ("revenue", "FY2027", "5000000")],
    )
    analysis = analyze(settings, fields, payload)
    assert "NO_ACTUALS" in codes(analysis)
    assert "actual_periods" in gap_fields(analysis)


def test_terminal_year_market_share(settings, fields):
    payload = make_payload(
        periods=[("FY2025", "actual"), ("FY2028", "projected")],
        series=[("revenue", "FY2025", "1000000"), ("revenue", "FY2028", "400000000")],
        metrics=[("tam", "1000000000", "USD")],
    )
    analysis = analyze(settings, fields, payload)
    flag = next(f for f in analysis.flags if f.code == "UNSUPPORTED_TERMINAL_YEAR")
    assert "40%" in flag.finding


def test_missing_tam_is_a_gap_not_a_guess(settings, fields):
    payload = make_payload(
        periods=[("FY2025", "actual"), ("FY2028", "projected")],
        series=[("revenue", "FY2025", "1000000"), ("revenue", "FY2028", "400000000")],
    )
    analysis = analyze(settings, fields, payload)
    assert "UNSUPPORTED_TERMINAL_YEAR" not in codes(analysis)
    assert "tam" in gap_fields(analysis)


def test_implausibly_low_burn(settings, fields):
    payload = make_payload(
        metrics=[("burn_monthly", "60000", "USD"), ("headcount", "22", "people")],
    )
    analysis = analyze(settings, fields, payload)
    flag = next(f for f in analysis.flags if f.code == "IMPLAUSIBLY_LOW_BURN")
    assert "22" in flag.finding
    assert "$2,727" in flag.finding or "$2,7" in flag.finding


def test_costs_do_not_follow_revenue(settings, fields):
    payload = make_payload(
        periods=[("FY2025", "actual"), ("FY2027", "projected")],
        series=[
            ("revenue", "FY2025", "1000000"),
            ("revenue", "FY2027", "5000000"),
            ("opex", "FY2025", "2000000"),
            ("opex", "FY2027", "2400000"),
        ],
    )
    analysis = analyze(settings, fields, payload)
    assert "BURN_FLAT_WHILE_REVENUE_SCALES" in codes(analysis)


def test_runway_mismatch_and_short_runway(settings, fields):
    payload = make_payload(
        metrics=[
            ("raise_ask", "3000000", "USD"),
            ("burn_monthly", "400000", "USD"),  # 7.5 months computed
            ("runway_months", "24", "months"),
        ],
    )
    analysis = analyze(settings, fields, payload)
    fired = codes(analysis)
    assert "RUNWAY_MISMATCH" in fired
    assert "RUNWAY_TOO_SHORT" in fired
    mismatch = next(f for f in analysis.flags if f.code == "RUNWAY_MISMATCH")
    assert "24" in mismatch.finding and "8" in mismatch.finding


def test_ltv_cac_unrealistic_and_undisclosed_method(settings, fields):
    payload = make_payload(metrics=[("cac", "400", "USD"), ("ltv", "9000", "USD")])
    analysis = analyze(settings, fields, payload)
    fired = codes(analysis)
    assert "LTV_CAC_UNREALISTIC" in fired
    assert "LTV_METHOD_UNDISCLOSED" in fired
    ratio_flag = next(f for f in analysis.flags if f.code == "LTV_CAC_UNREALISTIC")
    assert ratio_flag.severity == "critical"  # 22.5x is above the critical ceiling


def test_ltv_below_cac_is_critical(settings, fields):
    payload = make_payload(metrics=[("cac", "9000", "USD"), ("ltv", "4000", "USD")])
    analysis = analyze(settings, fields, payload)
    flag = next(f for f in analysis.flags if f.code == "LTV_CAC_UNDERWATER")
    assert flag.severity == "critical"


def test_missing_cash_flow_is_critical_when_rigor_is_claimed(settings, fields):
    payload = make_payload(
        periods=[("FY2026", "projected")],
        series=[("revenue", "FY2026", "1000000")],
        claims={"claims_financial_rigor": True},
    )
    analysis = analyze(settings, fields, payload)
    flag = next(f for f in analysis.flags if f.code == "MISSING_CASH_FLOW")
    assert flag.severity == "critical"
    assert flag.evidence == []  # absence codes are evidence-exempt
    assert "cash_flow" in gap_fields(analysis)


def test_completeness_is_a_gap_only_when_no_rigor_is_claimed(settings, fields):
    payload = make_payload(claims={"claims_financial_rigor": False})
    analysis = analyze(settings, fields, payload)
    assert "MISSING_CASH_FLOW" not in codes(analysis)
    assert "cash_flow" in gap_fields(analysis)


def test_deck_and_model_disagreement(settings, fields):
    deck = make_payload(metrics=[("arr", "1200000", "USD")])
    model = make_payload(metrics=[("arr", "1560000", "USD")], locator="Model!B4", method="cell")
    analysis = analyze(settings, fields, deck, model)
    flag = next(f for f in analysis.flags if f.code == "DATA_INCONSISTENCY")
    assert "p.7" in flag.finding and "Model!B4" in flag.finding
    assert "$1.2M" in flag.finding and "$1.6M" in flag.finding


def test_stale_data_needs_a_deck_date(settings, fields):
    payload = make_payload(
        deck_date="",
        periods=[("FY2024", "actual"), ("FY2026", "projected")],
        series=[("revenue", "FY2024", "1000000"), ("revenue", "FY2026", "2000000")],
    )
    analysis = analyze(settings, fields, payload)
    assert "STALE_DATA" not in codes(analysis)
    assert "deck_date" in gap_fields(analysis)


def test_stale_data_fires_with_a_deck_date(settings, fields):
    payload = make_payload(
        deck_date="2026-08-01",
        periods=[("FY2024", "actual"), ("FY2026", "projected")],
        series=[("revenue", "FY2024", "1000000"), ("revenue", "FY2026", "2000000")],
    )
    analysis = analyze(settings, fields, payload)
    flag = next(f for f in analysis.flags if f.code == "STALE_DATA")
    assert "FY2024" in flag.finding


def test_empty_analysis_never_raises(settings, fields):
    """A deck that yielded nothing produces a report full of gaps, not a traceback."""
    empty = make_payload(claims={"claims_financial_rigor": False})
    analysis = analyze(settings, fields, empty)
    assert analysis.flags == [] or all(f.code in evidence_exempt_codes() for f in analysis.flags)
    assert analysis.gaps
    assert 0 <= analysis.grounding_score <= 100


def test_flags_are_sorted_by_severity_then_code(settings, fields):
    analysis = analyze(settings, fields, HOCKEY_STICK)
    ranks = [("critical", "high", "medium", "info").index(f.severity) for f in analysis.flags]
    assert ranks == sorted(ranks)


def test_headcount_grows_faster_than_cost(settings, fields):
    payload = make_payload(
        periods=[("FY2025", "actual"), ("FY2027", "projected")],
        series=[
            ("headcount", "FY2025", "20"),
            ("headcount", "FY2027", "40"),
            ("opex", "FY2025", "3000000"),
            ("opex", "FY2027", "3300000"),
        ],
    )
    flag = next(
        f for f in analyze(settings, fields, payload).flags if f.code == "OPEX_LAGS_HEADCOUNT"
    )
    assert "2.0x" in flag.finding and "1.1x" in flag.finding


def test_ltv_computed_on_revenue_not_gross_profit(settings, fields):
    payload = make_payload(
        metrics=[
            ("ltv", "9000", "USD"),
            ("arpu", "750", "USD"),
            ("customer_lifetime_months", "12", "months"),
            ("gross_margin", "0.6", "%"),
            ("cac", "1800", "USD"),
            ("churn_rate", "0.05", "%"),
        ],
    )
    flag = next(
        f for f in analyze(settings, fields, payload).flags if f.code == "LTV_IGNORES_MARGIN"
    )
    assert "$5,400" in flag.finding  # margin-adjusted LTV
    assert "60%" in flag.finding


def test_cac_payback_uses_the_sector_ceiling(settings, fields):
    payload = make_payload(
        metrics=[("cac", "1000", "USD"), ("cac_payback_months", "30", "months")],
    )
    flag = next(f for f in analyze(settings, fields, payload).flags if f.code == "CAC_PAYBACK_LONG")
    assert "30" in flag.finding and "18" in flag.finding
    assert "B2B" in flag.finding


def test_zero_churn_implies_an_unbounded_lifetime(settings, fields):
    payload = make_payload(metrics=[("ltv", "9000", "USD"), ("churn_rate", "0", "%")])
    assert "CHURN_IMPLIES_INFINITE_LIFETIME" in codes(analyze(settings, fields, payload))


def test_flat_cac_while_volume_scales(settings, fields):
    payload = make_payload(
        periods=[("FY2025", "actual"), ("FY2027", "projected")],
        series=[
            ("cac", "FY2025", "400"),
            ("cac", "FY2027", "395"),
            ("customers", "FY2025", "100"),
            ("customers", "FY2027", "900"),
        ],
    )
    flag = next(
        f for f in analyze(settings, fields, payload).flags if f.code == "CAC_STATIC_ACROSS_PLAN"
    )
    assert "9.0x" in flag.finding


def test_revenue_scaling_without_go_to_market_spend(settings, fields):
    payload = make_payload(
        periods=[("FY2025", "actual"), ("FY2027", "projected")],
        series=[
            ("revenue", "FY2025", "1000000"),
            ("revenue", "FY2027", "5000000"),
            ("sales_marketing_spend", "FY2025", "500000"),
            ("sales_marketing_spend", "FY2027", "510000"),
        ],
    )
    assert "ORGANIC_GROWTH_ASSUMED" in codes(analyze(settings, fields, payload))


def test_stated_margin_that_does_not_tie(settings, fields):
    payload = make_payload(
        periods=[("FY2025", "actual")],
        series=[
            ("revenue", "FY2025", "1000000"),
            ("cogs", "FY2025", "600000"),
            ("gross_margin", "FY2025", "0.8"),  # actually 40%
        ],
    )
    flag = next(f for f in analyze(settings, fields, payload).flags if f.code == "ARITHMETIC_ERROR")
    assert "80%" in flag.finding and "40%" in flag.finding


def test_scale_ambiguous_revenue_series(settings, fields):
    payload = make_payload(
        periods=[("FY2025", "actual"), ("FY2026", "projected")],
        series=[("revenue", "FY2025", "1.2"), ("revenue", "FY2026", "4.8")],
    )
    assert "UNIT_AMBIGUITY" in codes(analyze(settings, fields, payload))


def test_a_failing_rule_family_never_takes_the_run_down(settings, fields, monkeypatch):
    from deckscan.analyze import engine

    def explode(analysis, settings):
        raise ZeroDivisionError("synthetic")

    monkeypatch.setattr(engine, "FAMILIES", (("growth", explode), *engine.FAMILIES[1:]))
    analysis = analyze(settings, fields, HOCKEY_STICK)
    assert any("growth rules could not be evaluated" in note for note in analysis.methodology)
    assert "HOCKEY_STICK_REVENUE" not in codes(analysis)
    assert analysis.gaps  # the other families still ran


def test_grounding_score_penalises_flags(settings, fields):
    clean = analyze(
        settings,
        fields,
        make_payload(
            claims=dict.fromkeys(
                [
                    "has_cash_flow",
                    "has_income_statement_detail",
                    "has_balance_sheet",
                    "has_assumptions",
                    "has_sensitivity_case",
                    "has_cap_table",
                    "has_use_of_funds",
                    "has_pricing",
                    "has_tam_methodology",
                    "has_headcount_plan",
                ],
                True,
            )
        ),
    )
    noisy = analyze(settings, fields, HOCKEY_STICK)
    assert clean.grounding_score > noisy.grounding_score
