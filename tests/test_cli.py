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
    bad = tmp_path / "deck.key"
    bad.write_text("not a deck", encoding="utf-8")
    result = runner.invoke(app, ["analyze", str(bad)])
    assert result.exit_code == 1
    assert "unsupported deck type" in result.output


def test_unsupported_model_extension_exits_1(tmp_path, tiny_pdf):
    model = tmp_path / "model.numbers"
    model.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["analyze", str(tiny_pdf), "--model", str(model)])
    assert result.exit_code == 1
    assert "unsupported model type" in result.output


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
