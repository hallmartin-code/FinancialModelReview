from __future__ import annotations

import textwrap

import pytest

from deckscan.config import clear_config_cache, load_config
from deckscan.models import SEVERITY_RANK, evidence_exempt_codes

# Codes named in the build spec that must exist in rules.yaml.
SPEC_FLAG_CODES = {
    "HOCKEY_STICK_REVENUE",
    "GROWTH_DISCONTINUITY",
    "FLAT_THEN_SPIKE",
    "UNSUPPORTED_TERMINAL_YEAR",
    "NO_ACTUALS",
    "IMPLAUSIBLY_LOW_BURN",
    "OPEX_LAGS_HEADCOUNT",
    "BURN_FLAT_WHILE_REVENUE_SCALES",
    "RUNWAY_MISMATCH",
    "RUNWAY_TOO_SHORT",
    "NO_HEADCOUNT_PLAN",
    "LTV_CAC_UNREALISTIC",
    "LTV_CAC_UNDERWATER",
    "LTV_METHOD_UNDISCLOSED",
    "LTV_IGNORES_MARGIN",
    "CAC_PAYBACK_LONG",
    "CHURN_IMPLIES_INFINITE_LIFETIME",
    "CAC_STATIC_ACROSS_PLAN",
    "ORGANIC_GROWTH_ASSUMED",
    "MISSING_CASH_FLOW",
    "MISSING_INCOME_STATEMENT_DETAIL",
    "MISSING_BALANCE_SHEET",
    "MISSING_ASSUMPTIONS_PAGE",
    "NO_SENSITIVITY_CASE",
    "PROJECTION_HORIZON_SHORT",
    "MISSING_CAP_TABLE_OR_PRIOR_RAISES",
    "MISSING_USE_OF_FUNDS",
    "MISSING_PRICING",
    "MISSING_TAM_METHODOLOGY",
    "DATA_INCONSISTENCY",
    "ARITHMETIC_ERROR",
    "UNIT_AMBIGUITY",
    "STALE_DATA",
}


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_config_cache()
    yield
    clear_config_cache()


def test_packaged_config_loads():
    settings = load_config()
    assert settings.version == 1
    assert settings.extraction.min_chars_per_page == 120


def test_every_spec_flag_code_has_text():
    settings = load_config()
    missing = SPEC_FLAG_CODES - set(settings.rule_text)
    assert not missing, f"rules.yaml is missing text for: {sorted(missing)}"


def test_rule_severities_are_valid():
    settings = load_config()
    for code, text in settings.rule_text.items():
        assert text.severity in SEVERITY_RANK, code
        if text.critical_severity is not None:
            assert text.critical_severity in SEVERITY_RANK, code


def test_gap_severities_are_valid():
    settings = load_config()
    for field, text in settings.gap_text.items():
        assert text.severity in SEVERITY_RANK, field
        assert text.ask.strip(), field


def test_evidence_exempt_codes_all_exist():
    settings = load_config()
    unknown = set(settings.evidence_exempt_codes) - set(settings.rule_text)
    assert not unknown, f"evidence_exempt_codes names unknown codes: {sorted(unknown)}"


def test_completeness_codes_are_evidence_exempt():
    settings = load_config()
    completeness = {c for c in settings.rule_text if c.startswith(("MISSING_", "NO_"))}
    completeness |= {"PROJECTION_HORIZON_SHORT"}
    exempt = set(settings.evidence_exempt_codes)
    assert completeness <= exempt, sorted(completeness - exempt)


def test_loading_installs_exempt_codes_on_models():
    settings = load_config()
    assert evidence_exempt_codes() == frozenset(settings.evidence_exempt_codes)


def test_headline_metrics_are_known_metric_names():
    settings = load_config()
    unknown = set(settings.headline_metrics) - set(settings.metric_aliases)
    assert not unknown, sorted(unknown)


def test_override_deep_merges_over_packaged_defaults(tmp_path):
    override = tmp_path / "override.yaml"
    override.write_text(
        textwrap.dedent(
            """
            thresholds:
              growth:
                hockey_stick_multiple: 2.5
            """
        ),
        encoding="utf-8",
    )
    settings = load_config(override)
    assert settings.thresholds.growth.hockey_stick_multiple == 2.5
    # Untouched sibling keys survive the merge.
    assert settings.thresholds.growth.growth_discontinuity_ratio == 2.0
    assert settings.thresholds.burn.min_monthly_cost_per_head == 8000


def test_band_labels():
    settings = load_config()
    assert settings.band_key(95) == "well_grounded"
    assert settings.band_key(80) == "well_grounded"
    assert settings.band_key(79) == "needs_grounding"
    assert settings.band_key(50) == "needs_grounding"
    assert settings.band_key(49) == "weakly_grounded"
    assert settings.band_label(0) == "Weakly grounded"


def test_severity_colors_cover_every_severity():
    settings = load_config()
    for severity in SEVERITY_RANK:
        assert settings.render.severity_colors[severity].startswith("#")
