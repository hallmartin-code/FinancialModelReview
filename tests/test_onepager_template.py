"""Structural checks on the packaged one-pager document template.

The template declares both the fields analyzed in every source document and the
layout of the generated document. These tests keep the two halves in step and
keep company-specific content out of the template.
"""

from __future__ import annotations

import json
from importlib import resources

import pytest

REQUIRED_SECTIONS = (
    "analysis_fields",
    "page",
    "palette",
    "typography",
    "header",
    "body",
    "footer",
    "analysis",
)


@pytest.fixture(scope="module")
def template() -> dict:
    resource = resources.files("deckscan").joinpath("config/onepager_template.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def test_template_has_every_required_section(template):
    missing = [key for key in REQUIRED_SECTIONS if key not in template]
    assert not missing, f"template is missing: {missing}"


def test_every_rendered_field_is_declared_for_analysis(template):
    declared = set(template["analysis_fields"])
    rendered = {element["field"] for element in template["header"]["elements"]}
    rendered |= {
        section["field"] for column in template["body"]["columns"] for section in column["sections"]
    }
    rendered.add(template["footer"]["right_field"])
    assert rendered <= declared, f"rendered but not analyzed: {sorted(rendered - declared)}"


def test_every_analyzed_field_is_rendered_somewhere(template):
    declared = set(template["analysis_fields"])
    rendered = {element["field"] for element in template["header"]["elements"]}
    rendered |= {
        section["field"] for column in template["body"]["columns"] for section in column["sections"]
    }
    rendered.add(template["footer"]["right_field"])
    assert declared <= rendered, f"analyzed but never rendered: {sorted(declared - rendered)}"


def test_placeholders_are_field_tokens(template):
    for column in template["body"]["columns"]:
        for section in column["sections"]:
            assert section["placeholder"] == "{{" + section["field"] + "}}", section["label"]
    for element in template["header"]["elements"]:
        assert element["placeholder"] == "{{" + element["field"] + "}}", element["field"]


def test_column_widths_fill_the_page(template):
    total = sum(column["width_ratio"] for column in template["body"]["columns"])
    assert total == pytest.approx(1.0)


def test_every_named_color_exists_in_the_palette(template):
    palette = set(template["palette"])
    used = {template["header"]["background"], template["footer"]["background"]}
    used |= {template["footer"]["color"]}
    used |= {element["color"] for element in template["header"]["elements"]}
    assert used <= palette, f"unknown palette colors: {sorted(used - palette)}"


def test_layout_fits_one_page(template):
    page = template["page"]
    assert page["max_pages"] == 1
    body = template["body"]
    used = (
        template["header"]["height_pt"]
        + body["top_padding_pt"]
        + body["bottom_padding_pt"]
        + template["analysis"]["height_pt"]
        + template["footer"]["height_pt"]
    )
    # The bands are fixed furniture; what is left is what the narrative may use.
    assert used < page["height_pt"]
    assert page["height_pt"] - used > 200, "no usable room left for the narrative"


def test_analysis_band_is_declared(template):
    band = template["analysis"]
    assert band["max_flags"] >= 1
    assert band["max_grounding_items"] >= 1
    assert set(band["severity_colors"]) == {"critical", "high", "medium", "info"}
    for name in band["severity_colors"].values():
        assert name in template["palette"], name
    assert band["background"] in template["palette"]
    assert band["rule_color"] in template["palette"]


def test_template_carries_no_company_content(template):
    """Only structure, labels, placeholders and style - never extracted values."""
    blob = json.dumps(template).lower()
    for token in ("halcyon", "@", "inc.", "llc", "$"):
        assert token not in blob, f"template contains company-specific content: {token!r}"


def test_overflow_rules_match_the_one_page_requirement(template):
    rules = template["rules"]
    assert rules["empty_fields"] == "omit_section"
    assert rules["never_shrink_type"] is True
    assert rules["invented_content"] == "forbidden"
