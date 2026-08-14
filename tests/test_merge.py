"""Merging deck and model payloads: provenance, precedence, and survival."""

from __future__ import annotations

from decimal import Decimal

from conftest import make_payload
from deckscan.parse.merge import build_analysis
from deckscan.parse.normalize import to_decimal


def _build(settings, fields, deck, model=None):
    return build_analysis(
        settings=settings, narrative_fields=fields, deck_payload=deck, model_payload=model
    )


def test_values_carry_their_locator_and_method(settings, fields):
    analysis = _build(settings, fields, make_payload(metrics=[("arr", "1200000", "USD")]))
    metric = analysis.primary("arr")
    assert metric is not None
    assert metric.value == Decimal("1200000")
    assert metric.provenance[0].locator == "p.7"
    assert metric.provenance[0].method == "table"
    assert metric.provenance[0].source == "deck"


def test_conflicting_values_both_survive(settings, fields):
    deck = make_payload(metrics=[("arr", "1200000", "USD")])
    model = make_payload(metrics=[("arr", "1560000", "USD")], locator="Model!B4", method="cell")
    analysis = _build(settings, fields, deck, model)
    assert len(analysis.metrics["arr"]) == 2
    values = {m.value for m in analysis.metrics["arr"]}
    assert values == {Decimal("1200000"), Decimal("1560000")}


def test_model_is_the_authoritative_primary(settings, fields):
    deck = make_payload(metrics=[("arr", "1200000", "USD")])
    model = make_payload(metrics=[("arr", "1560000", "USD")], locator="Model!B4", method="cell")
    analysis = _build(settings, fields, deck, model)
    primary = analysis.primary("arr")
    assert primary is not None
    assert primary.value == Decimal("1560000")


def test_model_series_replaces_the_deck_series(settings, fields):
    deck = make_payload(
        periods=[("FY2025", "actual")],
        series=[("revenue", "FY2025", "1000000")],
    )
    model = make_payload(
        periods=[("FY2025", "actual")],
        series=[("revenue", "FY2025", "1450000")],
        locator="Model!C3",
        method="cell",
    )
    analysis = _build(settings, fields, deck, model)
    assert analysis.series["revenue"] == [("FY2025", Decimal("1450000"))]


def test_periods_keep_their_actual_projected_split(settings, fields):
    analysis = _build(
        settings,
        fields,
        make_payload(periods=[("FY2024", "actual"), ("FY2026", "projected")]),
    )
    assert analysis.actual_periods == ["FY2024"]
    assert analysis.projected_periods == ["FY2026"]


def test_series_are_ordered_actuals_then_projections(settings, fields):
    payload = make_payload(
        periods=[("FY2024", "actual"), ("FY2025", "projected")],
        series=[("revenue", "FY2025", "2000000"), ("revenue", "FY2024", "1000000")],
    )
    analysis = _build(settings, fields, payload)
    assert [label for label, _ in analysis.series["revenue"]] == ["FY2024", "FY2025"]


def test_claims_are_true_if_any_source_has_it(settings, fields):
    deck = make_payload(claims={"has_cash_flow": False})
    model = make_payload(claims={"has_cash_flow": True})
    analysis = _build(settings, fields, deck, model)
    assert analysis.claims["has_cash_flow"] is True


def test_empty_payloads_produce_an_empty_analysis(settings, fields):
    analysis = _build(settings, fields, None, None)
    assert analysis.metrics == {}
    assert analysis.company is None
    assert all(value == "" for value in analysis.narrative.values())


def test_normalizer_reads_the_shapes_that_slip_through(settings):
    assert to_decimal("4200000", settings) == Decimal("4200000")
    assert to_decimal("$4.2M", settings) == Decimal("4200000")
    assert to_decimal("1,250", settings) == Decimal("1250")
    assert to_decimal("65%", settings) == Decimal("0.65")
    assert to_decimal("(500)", settings) == Decimal("-500")
    assert to_decimal("not disclosed", settings) is None
    assert to_decimal("", settings) is None
    assert to_decimal(None, settings) is None
