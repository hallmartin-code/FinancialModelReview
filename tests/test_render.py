"""Both PDFs must be exactly one page — including when overloaded."""

from __future__ import annotations

from pypdf import PdfReader

from conftest import analyze, make_payload
from deckscan.render.narrative import render_narrative, render_template_skeleton
from deckscan.render.onepager import render_onepager

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


def test_financial_screen_is_one_page(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload(narrative=NARRATIVE))
    out = render_onepager(analysis, tmp_path / "screen.pdf", settings, sources=["deck.pdf"])
    assert _pages(out) == 1


def test_overloaded_screen_still_fits_one_page(settings, fields, tmp_path):
    analysis = analyze(settings, fields, _overloaded_payload())
    assert len(analysis.flags) >= 8, "fixture should overload the page"
    out = render_onepager(analysis, tmp_path / "overloaded.pdf", settings, sources=["deck.pdf"])
    assert _pages(out) == 1


def test_empty_analysis_still_renders_one_page(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload(claims={"claims_financial_rigor": False}))
    out = render_onepager(analysis, tmp_path / "empty.pdf", settings)
    assert _pages(out) == 1


def test_narrative_one_pager_is_one_page(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload(narrative=NARRATIVE))
    out = render_narrative(analysis, tmp_path / "onepager.pdf")
    assert _pages(out) == 1


def test_narrative_omits_sections_it_has_no_content_for(settings, fields, tmp_path):
    sparse = analyze(settings, fields, make_payload(narrative={"company_name": "Acme"}))
    out = render_narrative(sparse, tmp_path / "sparse.pdf")
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "Acme" in text
    assert "PROBLEM" not in text  # nothing extracted, so no empty section


def test_template_skeleton_renders_placeholders(tmp_path):
    out = render_template_skeleton(tmp_path / "template.pdf")
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "{{problem}}" in text
    assert "PROBLEM" in text
    assert _pages(out) == 1


def test_screen_shows_not_disclosed_rather_than_zero(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload())
    out = render_onepager(analysis, tmp_path / "tiles.pdf", settings)
    text = PdfReader(str(out)).pages[0].extract_text()
    assert settings.render.not_disclosed_label in text
    assert "$0" not in text


HOCKEY_STICK_REPORT = {
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


def test_investor_report_carries_the_red_flag_analysis(settings, fields, tmp_path):
    """The four checks the brief names must reach the investor report itself."""
    analysis = analyze(settings, fields, make_payload(**HOCKEY_STICK_REPORT))
    out = render_narrative(analysis, tmp_path / "report.pdf", settings=settings)
    text = PdfReader(str(out)).pages[0].extract_text()

    assert "RED FLAG ANALYSIS" in text
    assert "NEEDS GROUNDING" in text
    assert "GROUNDING" in text and "/100" in text
    # Severity is a word as well as a colour, so the page survives grayscale.
    assert "CRITICAL" in text or "HIGH" in text
    # The narrative it sits under is still there.
    assert "PROBLEM" in text and "Northwind Analytics" in text


def test_report_flags_quote_their_numbers(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload(**HOCKEY_STICK_REPORT))
    out = render_narrative(analysis, tmp_path / "report.pdf", settings=settings)
    text = PdfReader(str(out)).pages[0].extract_text()
    band = text.split("RED FLAG ANALYSIS")[1]
    assert any(char.isdigit() for char in band)
    assert "22.5x" in band or "8.0x" in band or "1.1x" in band


def test_report_says_how_many_flags_it_could_not_show(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload(**HOCKEY_STICK_REPORT))
    assert len(analysis.flags) > 4, "fixture should overflow the band"
    out = render_narrative(analysis, tmp_path / "report.pdf", settings=settings)
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "more in the financial screen" in text


def test_report_with_flags_is_still_one_page(settings, fields, tmp_path):
    analysis = analyze(settings, fields, make_payload(**HOCKEY_STICK_REPORT))
    out = render_narrative(analysis, tmp_path / "report.pdf", settings=settings)
    assert _pages(out) == 1


def test_clean_report_says_so_rather_than_leaving_the_band_blank(settings, fields, tmp_path):
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
    out = render_narrative(analysis, tmp_path / "clean.pdf", settings=settings)
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "No red flags fired" in text


def test_skeleton_shows_the_analysis_band_placeholders(tmp_path):
    out = render_template_skeleton(tmp_path / "template.pdf")
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "RED FLAG ANALYSIS" in text
    assert "{{red_flag}}" in text
    assert "{{needs_grounding}}" in text


def test_grounding_list_never_repeats_a_subject(settings, fields):
    """A completeness flag and its gap must not take two of the slots."""
    from deckscan.analyze.scoring import grounding_items

    analysis = analyze(settings, fields, make_payload(**HOCKEY_STICK_REPORT))
    items = grounding_items(analysis, settings)
    claims = [item.claim.lower() for item in items]
    assert len(claims) == len(set(claims)), claims


def _unread(settings, fields):
    """An analysis where extraction produced nothing at all.

    Mirrors the live failure: nothing read, so nothing claims financial rigor
    either, and no completeness flag can fire.
    """
    analysis = analyze(settings, fields, make_payload(claims={"claims_financial_rigor": False}))
    assert analysis.nothing_extracted()
    assert not analysis.flags, "the unread path fires no rules"
    return analysis


def test_unread_report_is_not_a_branded_blank_page(settings, fields, tmp_path):
    """A deck nobody could read must say so, not render as a logo over a void."""
    analysis = _unread(settings, fields)
    out = render_narrative(analysis, tmp_path / "unread.pdf", settings=settings)
    text = PdfReader(str(out)).pages[0].extract_text()

    assert "NOTHING COULD BE READ FROM THIS DECK" in text
    assert "not a clean bill of health" in text
    # The header never renders empty.
    assert "Company not identified" in text
    # And the reader still gets the list of what is needed.
    assert "NEEDS GROUNDING" in text


def test_unread_report_does_not_claim_a_clean_result(settings, fields, tmp_path):
    analysis = _unread(settings, fields)
    out = render_narrative(analysis, tmp_path / "unread.pdf", settings=settings)
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "No red flags fired" not in text
    assert "not a clean result" in text


def test_unread_screen_does_not_claim_a_clean_result(settings, fields, tmp_path):
    analysis = _unread(settings, fields)
    out = render_onepager(analysis, tmp_path / "unread-screen.pdf", settings)
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "No red flags fired" not in text
    assert "unread one" in text or "no rule could be evaluated" in text


def test_unread_documents_are_still_one_page(settings, fields, tmp_path):
    analysis = _unread(settings, fields)
    report = render_narrative(analysis, tmp_path / "a.pdf", settings=settings)
    screen = render_onepager(analysis, tmp_path / "b.pdf", settings)
    assert _pages(report) == 1
    assert _pages(screen) == 1


def test_a_deck_that_was_read_with_no_flags_still_reads_as_clean(settings, fields, tmp_path):
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
    text = (
        PdfReader(str(render_narrative(analysis, tmp_path / "clean.pdf", settings=settings)))
        .pages[0]
        .extract_text()
    )
    assert "No red flags fired" in text
    assert "NOTHING COULD BE READ" not in text
