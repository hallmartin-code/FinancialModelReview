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
