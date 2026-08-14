"""The extraction request shape, checked without touching the network."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from reportlab.pdfgen import canvas

from deckscan.extract import claude as claude_module
from deckscan.extract.schema import extraction_schema
from deckscan.extract.source import prepare_deck, prepare_model


def _walk_objects(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_walk_objects(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_objects(item))
    return found


def test_schema_is_closed_and_fully_required(settings, fields):
    """Structured outputs reject open objects and partial `required` lists."""
    schema = extraction_schema(settings, fields)
    for obj in _walk_objects(schema):
        assert obj.get("additionalProperties") is False
        assert set(obj.get("required", [])) == set(obj.get("properties", {}))


def test_schema_asks_for_every_template_field(settings, fields):
    schema = extraction_schema(settings, fields)
    assert set(schema["properties"]["narrative"]["properties"]) == set(fields)


def test_schema_metric_names_are_the_configured_ones(settings, fields):
    schema = extraction_schema(settings, fields)
    names = schema["properties"]["metrics"]["items"]["properties"]["name"]["enum"]
    assert set(names) == set(settings.metric_aliases)


# --- source preparation ------------------------------------------------------


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "deck.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 720, "Northwind Analytics")
    pdf.showPage()
    pdf.drawString(72, 720, "FY2025 revenue $1.2M")
    pdf.save()
    return path


def test_pdf_is_sent_as_a_native_document_block(pdf_path, settings):
    content = prepare_deck(pdf_path, settings)
    assert content.blocks[0]["type"] == "document"
    assert content.blocks[0]["source"]["media_type"] == "application/pdf"
    assert any("native PDF" in note for note in content.methodology)


def test_word_deck_is_flattened_with_page_markers(tmp_path, settings):
    from docx import Document

    path = tmp_path / "deck.docx"
    document = Document()
    document.add_paragraph("Northwind Analytics")
    document.add_paragraph("FY2025 revenue $1.2M")
    document.save(str(path))

    content = prepare_deck(path, settings)
    assert not content.failed
    assert content.inline_text is not None
    assert "[p.1]" in content.inline_text
    assert "Northwind Analytics" in content.inline_text


def test_csv_model_keeps_cell_references(tmp_path):
    path = tmp_path / "model.csv"
    path.write_text("Period,Revenue\nFY2025,1200000\n", encoding="utf-8")
    content = prepare_model(path)
    assert content.inline_text is not None
    assert "A1=Period" in content.inline_text
    assert "B2=1200000" in content.inline_text


def test_unreadable_source_degrades_instead_of_raising(tmp_path, settings):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf at all")
    content = prepare_deck(path, settings)
    assert content.failed
    assert content.methodology


# --- the API call ------------------------------------------------------------


class _FakeStream:
    def __init__(self, message: Any) -> None:
        self._message = message

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_final_message(self) -> Any:
        return self._message


def _fake_client(captured: dict[str, Any], message: Any) -> Any:
    def stream(**kwargs: Any) -> _FakeStream:
        captured.update(kwargs)
        return _FakeStream(message)

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


def _message(text: str, stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
    )


def test_request_uses_opus_5_structured_output_and_fallbacks(
    monkeypatch, pdf_path, settings, fields
):
    captured: dict[str, Any] = {}
    payload = {"narrative": {}, "series": [], "metrics": [], "claims": {}}
    monkeypatch.setattr(
        claude_module, "get_client", lambda: _fake_client(captured, _message(json.dumps(payload)))
    )

    content = prepare_deck(pdf_path, settings)
    result = claude_module.extract(content, settings, fields)

    assert result == payload
    assert captured["model"] == "claude-opus-5"
    assert captured["betas"] == [claude_module.FALLBACK_BETA]
    assert captured["fallbacks"] == "default"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["output_config"]["effort"] == claude_module.EFFORT
    blocks = captured["messages"][0]["content"]
    assert blocks[0]["type"] == "document"
    assert blocks[-1]["type"] == "text"


def test_refusal_becomes_an_extraction_error(monkeypatch, pdf_path, settings, fields):
    monkeypatch.setattr(
        claude_module,
        "get_client",
        lambda: _fake_client({}, _message("", stop_reason="refusal")),
    )
    content = prepare_deck(pdf_path, settings)
    with pytest.raises(claude_module.ExtractionError, match="declined"):
        claude_module.extract(content, settings, fields)


def test_truncation_becomes_an_extraction_error(monkeypatch, pdf_path, settings, fields):
    monkeypatch.setattr(
        claude_module,
        "get_client",
        lambda: _fake_client({}, _message("{", stop_reason="max_tokens")),
    )
    content = prepare_deck(pdf_path, settings)
    with pytest.raises(claude_module.ExtractionError, match="truncated"):
        claude_module.extract(content, settings, fields)


def test_missing_key_is_a_clear_error(settings, fields, pdf_path):
    content = prepare_deck(pdf_path, settings)
    with pytest.raises(claude_module.ExtractionError, match="ANTHROPIC_API_KEY"):
        claude_module.extract(content, settings, fields)
