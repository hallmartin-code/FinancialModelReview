"""Source routing and the deck/model/both combinations run_analysis accepts."""

from __future__ import annotations

import pytest

from conftest import gap_fields, make_payload
from deckscan import pipeline
from deckscan.pipeline import AnalysisRequest, InputError, resolve_inputs, run_analysis


@pytest.fixture
def csv_model(tmp_path):
    path = tmp_path / "model.csv"
    rows = ["Period,Revenue", "FY2024,1200000", "FY2025,3600000"]
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


@pytest.fixture
def pdf_deck(tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "deck.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 720, "Northwind Analytics")
    pdf.save()
    return path


# --- routing -----------------------------------------------------------------


def test_a_spreadsheet_argument_becomes_the_model(csv_model):
    deck, model = resolve_inputs(csv_model)
    assert deck is None
    assert model == csv_model


def test_a_deck_argument_stays_the_deck(pdf_deck, csv_model):
    assert resolve_inputs(pdf_deck, csv_model) == (pdf_deck, csv_model)
    assert resolve_inputs(pdf_deck) == (pdf_deck, None)


def test_xlsm_is_accepted_as_a_model(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "model.xlsm"
    Workbook().save(str(path))
    assert resolve_inputs(path) == (None, path)


def test_a_directory_is_not_a_source(tmp_path):
    with pytest.raises(InputError, match="not a file"):
        resolve_inputs(tmp_path / ".")


# --- run combinations --------------------------------------------------------


def _request(settings, deck=None, model=None):
    return AnalysisRequest(
        deck_path=deck,
        model_path=model,
        company_override=None,
        ocr_mode="auto",
        settings=settings,
    )


@pytest.fixture
def stub_extraction(monkeypatch):
    """Return the same payload for whatever source is read, without the API."""
    monkeypatch.setattr(pipeline, "api_key_present", lambda: True)
    payload = make_payload(
        narrative={"company_name": "Northwind Analytics"},
        periods=[("FY2024", "actual"), ("FY2025", "projected")],
        series=[("revenue", "FY2024", "1200000"), ("revenue", "FY2025", "3600000")],
        locator="Model!B4",
        method="cell",
    )
    monkeypatch.setattr(pipeline, "extract", lambda *args, **kwargs: payload)
    return payload


def test_a_read_model_with_no_deck_is_not_an_extraction_failure(
    settings, csv_model, stub_extraction
):
    """The failure gap is about reading nothing, not about lacking a deck."""
    analysis = run_analysis(_request(settings, model=csv_model))
    assert "extraction_failure" not in gap_fields(analysis)
    assert analysis.company == "Northwind Analytics"
    assert analysis.series["revenue"]


def test_a_model_only_run_says_so_in_the_methodology(settings, csv_model, stub_extraction):
    analysis = run_analysis(_request(settings, model=csv_model))
    assert any("No pitch deck supplied" in note for note in analysis.methodology)


def test_a_deck_that_cannot_be_read_is_still_an_extraction_failure(settings, tmp_path):
    empty = tmp_path / "deck.pdf"
    empty.write_bytes(b"not really a pdf")
    analysis = run_analysis(_request(settings, deck=empty))
    assert "extraction_failure" in gap_fields(analysis)
