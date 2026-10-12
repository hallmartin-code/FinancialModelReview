"""The financial analysis report: everything the rule engine found, nothing dropped."""

from __future__ import annotations

from pypdf import PdfReader

from conftest import analyze, make_payload
from deckscan.render.analysis import render_analysis

NARRATIVE = {
    "company_name": "Northwind Analytics",
    "tagline": "Forecasting for mid-market distributors",
    "problem": "Distributors run their demand plans in spreadsheets that nobody trusts. " * 3,
    "solution": "A forecasting engine that reads ERP history and publishes a weekly plan. " * 3,
    "why_now": "ERP vendors opened their data APIs in 2025. " * 3,
    "business_model": "Annual subscription priced per distribution centre. " * 3,
    "market_size": "TAM $8B across 40,000 mid-market distributors. " * 3,
    "traction": "22 paying customers and $1.2M ARR. " * 3,
    "team": "Founders from Blue Yonder and Flexport. " * 3,
    "stage": "Seed",
    "ask": "$6M on a priced round; 24 months of runway. " * 2,
    "key_risks_retired": "Two ERP integrations shipped and in production. " * 3,
    "contact": "founder@northwind.example",
}


def _text(path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(str(path)).pages)


def _pages(path) -> int:
    return len(PdfReader(str(path)).pages)


def _overloaded_payload() -> dict:
    """A deck engineered to trip a large number of rules at once."""
    return make_payload(
        narrative=NARRATIVE,
        periods=[
            ("FY2023", "actual"),
            ("FY2024", "actual"),
            ("FY2025", "projected"),
            ("FY2026", "projected"),
            ("FY2027", "projected"),
        ],
        series=[
            ("revenue", "FY2023", "900000"),
            ("revenue", "FY2024", "1000000"),
            ("revenue", "FY2025", "12000000"),
            ("revenue", "FY2026", "60000000"),
            ("revenue", "FY2027", "300000000"),
            ("opex", "FY2023", "3000000"),
            ("opex", "FY2027", "3300000"),
            ("headcount", "FY2023", "20"),
            ("headcount", "FY2027", "44"),
            ("customers", "FY2023", "10"),
            ("customers", "FY2027", "900"),
            ("cac", "FY2023", "400"),
            ("cac", "FY2027", "395"),
            ("sales_marketing_spend", "FY2023", "500000"),
            ("sales_marketing_spend", "FY2027", "520000"),
        ],
        metrics=[
            ("burn_monthly", "60000", "USD"),
            ("headcount", "22", "people"),
            ("raise_ask", "3000000", "USD"),
            ("runway_months", "36", "months"),
            ("cac", "400", "USD"),
            ("ltv", "9000", "USD"),
            ("tam", "100000000", "USD"),
            ("churn_rate", "0", "%"),
            ("arr", "1200000", "USD"),
        ],
        deck_date="2026-08-01",
    )


HOCKEY_STICK = {
    "narrative": NARRATIVE,
    "periods": [
        ("FY2024", "actual"),
        ("FY2025", "actual"),
        ("FY2026", "projected"),
        ("FY2027", "projected"),
    ],
    "series": [
        ("revenue", "FY2024", "1000000"),
        ("revenue", "FY2025", "1150000"),
        ("revenue", "FY2026", "9200000"),
        ("revenue", "FY2027", "46000000"),
        ("opex", "FY2024", "3000000"),
        ("opex", "FY2027", "3400000"),
    ],
    "metrics": [
        ("burn_monthly", "60000", "USD"),
        ("headcount", "22", "people"),
        ("cac", "400", "USD"),
        ("ltv", "9000", "USD"),
    ],
    "claims": {"has_cash_flow": False},
}


# --- the report exists and holds together ------------------------------------


def test_report_renders(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload(**HOCKEY_STICK))
    out = render_analysis(analysis, tmp_path / "report.pdf", settings, sources=["model.xlsx"])
    assert _pages(out) >= 1
    text = _text(out)
    assert "Northwind Analytics" in text
    for heading in ("RED FLAGS", "NEEDS GROUNDING", "MISSING FROM THE MODEL"):
        assert heading in text


def test_empty_analysis_still_renders(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload())
    out = render_analysis(analysis, tmp_path / "empty.pdf", settings)
    assert _pages(out) >= 1


def test_tiles_show_not_disclosed_rather_than_zero(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload())
    out = render_analysis(analysis, tmp_path / "tiles.pdf", settings)
    text = _text(out)
    assert settings.render.not_disclosed_label in text
    assert "$0" not in text


# --- completeness: the point of replacing the one-page screen -----------------


def test_every_flag_reaches_the_report(settings, fields, tmp_path):
    """The screen capped flags and dropped more to fit a page. This must not."""
    analysis = analyze(settings, fields, _overloaded_payload())
    assert len(analysis.flags) > 8, "this payload should overload a one-page layout"

    out = render_analysis(analysis, tmp_path / "overloaded.pdf", settings)
    assert _pages(out) > 1, "an overloaded report should paginate, not truncate"
    text = _text(out)
    for flag in analysis.flags:
        assert flag.title in text, f"flag dropped from the report: {flag.code}"
    assert "more in the JSON export" not in text


def test_each_flag_carries_its_reasoning_and_question(settings, fields, tmp_path):
    """finding / why it matters / ask is what makes the section an analysis."""
    analysis = analyze(settings, fields, _overloaded_payload())
    text = _text(render_analysis(analysis, tmp_path / "detail.pdf", settings))
    assert text.count("Why it matters:") == len(analysis.flags)
    assert text.count("Ask:") == len(analysis.flags)


def test_flags_quote_their_numbers_and_cite_where_they_were_read(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload(**HOCKEY_STICK))
    text = _text(render_analysis(analysis, tmp_path / "report.pdf", settings))
    assert any(char.isdigit() for char in text)
    located = [flag for flag in analysis.flags if flag.first_locator]
    assert located, "this payload should produce at least one located flag"
    assert "Read from:" in text


def test_every_gap_is_listed(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload(**HOCKEY_STICK))
    text = _text(render_analysis(analysis, tmp_path / "gaps.pdf", settings))
    for gap in analysis.gaps:
        assert gap.label in text, f"gap missing from the report: {gap.field}"


def test_every_grounding_item_is_listed(settings, fields, tmp_path):
    from deckscan.analyze.scoring import grounding_items

    analysis = analyze(settings, fields, _overloaded_payload())
    items = grounding_items(analysis, settings)
    text = _text(render_analysis(analysis, tmp_path / "grounding.pdf", settings))
    for item in items:
        assert item.claim in text, f"grounding item dropped: {item.code}"


def test_grounding_list_never_repeats_a_subject(settings, fields):
    """A completeness flag and its gap must not both be listed."""
    from deckscan.analyze.scoring import grounding_items

    analysis = analyze(settings, fields, make_payload(**HOCKEY_STICK))
    items = grounding_items(analysis, settings)
    claims = [item.claim.lower() for item in items]
    assert len(claims) == len(set(claims)), claims


# --- every page stands on its own --------------------------------------------


def test_every_page_carries_the_disclaimer_and_its_number(settings, fields, tmp_path):
    analysis = analyze(settings, fields, _overloaded_payload())
    out = render_analysis(analysis, tmp_path / "paged.pdf", settings)
    reader = PdfReader(str(out))
    assert len(reader.pages) > 1
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        assert "Automated analysis" in text, f"page {index} has no disclaimer"
        assert f"Page {index}" in text, f"page {index} is not numbered"


def test_methodology_notes_are_not_truncated(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload(**HOCKEY_STICK))
    analysis.methodology = [
        "Model read from all 6 populated tab(s) of 6: Cover, P&L, Cash Flow, "
        "Headcount, Unit Economics, Use of Funds. Every value carries its sheet "
        "and cell reference.",
        "No pitch deck supplied; the financial model northwind.xlsx is the only source.",
    ]
    text = _text(render_analysis(analysis, tmp_path / "method.pdf", settings, sources=["m.xlsx"]))
    assert "Use of Funds" in text, "the tail of a long methodology note was cut"
    assert "northwind.xlsx" in text
    assert "m.xlsx" in text


# --- read vs unread must never look alike ------------------------------------


def _unread(settings, fields):
    """An analysis where extraction produced nothing at all.

    Mirrors the live failure: nothing read, so nothing claims financial rigor
    either, and no completeness flag can fire.
    """
    analysis = analyze(settings, fields, make_payload(claims={"claims_financial_rigor": False}))
    assert analysis.nothing_extracted()
    assert not analysis.flags, "the unread path fires no rules"
    return analysis


def test_unread_report_does_not_claim_a_clean_result(settings, fields, tmp_path):
    analysis = _unread(settings, fields)
    text = _text(render_analysis(analysis, tmp_path / "unread.pdf", settings))
    assert "No red flags fired" not in text
    assert "not a clean result" in text


def test_a_source_that_was_read_with_no_flags_still_reads_as_clean(settings, fields, tmp_path):
    """The clean case must keep its own wording, not borrow the unread one."""
    clean = make_payload(
        narrative=NARRATIVE,
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
        ),
    )
    analysis = analyze(settings, fields, clean)
    assert not analysis.nothing_extracted()
    text = _text(render_analysis(analysis, tmp_path / "clean.pdf", settings))
    assert "No red flags fired" in text
    assert "not a clean result" not in text
