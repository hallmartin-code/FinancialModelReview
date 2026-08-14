from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from deckscan.models import (
    DeckAnalysis,
    Flag,
    Gap,
    Metric,
    Provenance,
    canonical_json,
    sort_flags,
    sort_gaps,
)


def prov(locator: str = "p.4", method: str = "table") -> Provenance:
    return Provenance(source="deck", locator=locator, method=method, confidence=0.85)


def test_flag_requires_evidence():
    with pytest.raises(ValidationError):
        Flag(
            code="HOCKEY_STICK_REVENUE",
            severity="high",
            title="t",
            finding="growth peaks at 8.0x",
            why_it_matters="w",
            ask="a",
            evidence=[],
        )


def test_flag_requires_a_number_in_finding():
    with pytest.raises(ValidationError):
        Flag(
            code="HOCKEY_STICK_REVENUE",
            severity="high",
            title="t",
            finding="growth looks aggressive",
            why_it_matters="w",
            ask="a",
            evidence=[prov()],
        )


def test_absence_codes_are_exempt_from_both_checks():
    flag = Flag(
        code="MISSING_CASH_FLOW",
        severity="critical",
        title="No cash flow projection",
        finding="No cash flow statement is present.",
        why_it_matters="w",
        ask="a",
    )
    assert flag.evidence == []
    assert flag.first_locator == ""


def test_flag_sort_is_severity_then_code_then_locator():
    flags = [
        Flag(
            code="B_CODE",
            severity="high",
            title="t",
            finding="1",
            why_it_matters="w",
            ask="a",
            evidence=[prov("p.9")],
        ),
        Flag(
            code="A_CODE",
            severity="high",
            title="t",
            finding="1",
            why_it_matters="w",
            ask="a",
            evidence=[prov("p.2")],
        ),
        Flag(
            code="Z_CODE",
            severity="critical",
            title="t",
            finding="1",
            why_it_matters="w",
            ask="a",
            evidence=[prov("p.1")],
        ),
    ]
    assert [f.code for f in sort_flags(flags)] == ["Z_CODE", "A_CODE", "B_CODE"]


def test_gap_sort_is_severity_then_field():
    gaps = [
        Gap(field="pricing", label="l", severity="medium", ask="a"),
        Gap(field="assumptions", label="l", severity="medium", ask="a"),
        Gap(field="cash_flow", label="l", severity="critical", ask="a"),
    ]
    assert [g.field for g in sort_gaps(gaps)] == ["cash_flow", "assumptions", "pricing"]


def test_primary_metric_selection():
    analysis = DeckAnalysis(
        metrics={
            "arr": [
                Metric(name="arr", value=Decimal("1000000"), unit="USD", provenance=[prov()]),
                Metric(name="arr", value=Decimal("1300000"), unit="USD", provenance=[prov("p.9")]),
            ]
        },
        primary_metric={"arr": 1},
    )
    primary = analysis.primary("arr")
    assert primary is not None
    assert primary.value == Decimal("1300000")
    assert analysis.primary("cac") is None


def test_primary_metric_survives_a_bad_index():
    analysis = DeckAnalysis(
        metrics={"arr": [Metric(name="arr", value=Decimal("5"), provenance=[prov()])]},
        primary_metric={"arr": 7},
    )
    primary = analysis.primary("arr")
    assert primary is not None
    assert primary.value == Decimal("5")


def test_counts_by_severity_includes_zero_entries():
    analysis = DeckAnalysis()
    assert analysis.counts_by_severity() == {"critical": 0, "high": 0, "medium": 0, "info": 0}


def test_canonical_json_is_stable_and_decimal_safe():
    analysis = DeckAnalysis(
        company="Acme",
        metrics={"arr": [Metric(name="arr", value=Decimal("1200000.50"), provenance=[prov()])]},
        series={"revenue": [("FY24", Decimal("1200000")), ("FY25", Decimal("3600000"))]},
    )
    first = canonical_json(analysis)
    second = canonical_json(analysis.model_copy(deep=True))
    assert first == second
    assert '"1200000.50"' in first
    assert first.endswith("\n")
    # Keys are sorted at every level.
    assert first.index('"company"') < first.index('"metrics"') < first.index('"series"')
