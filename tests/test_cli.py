from __future__ import annotations

import json

import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from typer.testing import CliRunner

from deckscan.cli import app

runner = CliRunner()


@pytest.fixture
def tiny_pdf(tmp_path):
    path = tmp_path / "acme.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 720, "Acme Inc - Series A")
    pdf.save()
    return path


def test_missing_deck_exits_1(tmp_path):
    result = runner.invoke(app, ["analyze", str(tmp_path / "nope.pdf")])
    assert result.exit_code == 1
    assert "deck not found" in result.output


def test_unsupported_extension_exits_1(tmp_path):
    bad = tmp_path / "deck.rtf"
    bad.write_text("not a deck", encoding="utf-8")
    result = runner.invoke(app, ["analyze", str(bad)])
    assert result.exit_code == 1
    assert "unsupported input type" in result.output
    assert ".xlsx" in result.output  # the message lists every accepted type


def test_unsupported_model_extension_exits_1(tmp_path, tiny_pdf):
    model = tmp_path / "model.tsv"
    model.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["analyze", str(tiny_pdf), "--model", str(model)])
    assert result.exit_code == 1
    assert "unsupported model type" in result.output


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("model.xls", "re-save the workbook as .xlsx"),
        ("deck.key", "export the deck as PDF"),
        ("model.numbers", "export the model as .xlsx or .csv"),
    ],
)
def test_near_miss_formats_say_how_to_convert(tmp_path, name, expected):
    """A .xls or a .key is a wrong export, not a wrong idea — say which."""
    bad = tmp_path / name
    bad.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["analyze", str(bad)])
    assert result.exit_code == 1
    assert expected in result.output


def test_analyze_without_a_key_still_produces_both_pdfs(tmp_path, tiny_pdf):
    screen = tmp_path / "screen.pdf"
    onepager = tmp_path / "onepager.pdf"
    result = runner.invoke(
        app,
        [
            "analyze",
            str(tiny_pdf),
            "--company",
            "Acme Inc",
            "--out",
            str(screen),
            "--narrative",
            str(onepager),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ANTHROPIC_API_KEY is not set" in result.output
    assert "Acme Inc" in result.output
    assert len(PdfReader(str(screen)).pages) == 1
    assert len(PdfReader(str(onepager)).pages) == 1


def test_json_export_is_written_and_stable(tmp_path, tiny_pdf):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for target in (first, second):
        result = runner.invoke(
            app,
            [
                "analyze",
                str(tiny_pdf),
                "--json",
                str(target),
                "--out",
                str(tmp_path / "s.pdf"),
                "--narrative",
                str(tmp_path / "n.pdf"),
            ],
        )
        assert result.exit_code == 0, result.output
    assert first.read_bytes() == second.read_bytes()
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["gaps"], "a deck that could not be read is all gaps"
    assert payload["grounding_score"] <= 100


def test_verbose_prints_methodology(tmp_path, tiny_pdf):
    result = runner.invoke(
        app,
        [
            "analyze",
            str(tiny_pdf),
            "-v",
            "--out",
            str(tmp_path / "s.pdf"),
            "--narrative",
            str(tmp_path / "n.pdf"),
        ],
    )
    assert result.exit_code == 0
    assert "computed deterministically" in result.output


def test_template_command_renders_the_skeleton(tmp_path):
    out = tmp_path / "template.pdf"
    result = runner.invoke(app, ["template", "--out", str(out)])
    assert result.exit_code == 0
    assert len(PdfReader(str(out)).pages) == 1


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip()


@pytest.fixture
def tiny_model(tmp_path):
    path = tmp_path / "acme-model.csv"
    rows = ["Period,Revenue", "FY2025,1200000", "FY2026,3600000"]
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


def test_a_spreadsheet_alone_is_a_complete_run(tmp_path, tiny_model):
    """No deck required: the model is a source in its own right."""
    screen = tmp_path / "screen.pdf"
    onepager = tmp_path / "onepager.pdf"
    result = runner.invoke(
        app,
        [
            "analyze",
            str(tiny_model),
            "--company",
            "Acme Inc",
            "--out",
            str(screen),
            "--narrative",
            str(onepager),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(PdfReader(str(screen)).pages) == 1
    assert len(PdfReader(str(onepager)).pages) == 1


def test_an_xlsx_alone_is_accepted(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "model.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Period", "Revenue"])
    sheet.append(["FY2025", 1200000])
    workbook.save(str(path))

    result = runner.invoke(
        app,
        [
            "analyze",
            str(path),
            "--out",
            str(tmp_path / "s.pdf"),
            "--narrative",
            str(tmp_path / "n.pdf"),
        ],
    )
    assert result.exit_code == 0, result.output


def test_model_only_run_names_the_model_as_the_only_source(tmp_path, tiny_model):
    result = runner.invoke(
        app,
        [
            "analyze",
            str(tiny_model),
            "-v",
            "--out",
            str(tmp_path / "s.pdf"),
            "--narrative",
            str(tmp_path / "n.pdf"),
        ],
    )
    assert result.exit_code == 0
    assert "No pitch deck supplied" in result.output


def test_two_spreadsheets_with_no_deck_exits_1(tmp_path, tiny_model):
    other = tmp_path / "other.xlsx"
    other.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["analyze", str(tiny_model), "--model", str(other)])
    assert result.exit_code == 1
    assert "two spreadsheets" in result.output


def test_missing_spreadsheet_source_exits_1(tmp_path):
    result = runner.invoke(app, ["analyze", str(tmp_path / "nope.xlsx")])
    assert result.exit_code == 1
    assert "model not found" in result.output
