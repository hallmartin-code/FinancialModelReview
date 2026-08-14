"""The JSON schema Claude fills, and the prompts that go with it.

Structured outputs guarantee the response is valid JSON matching this schema, so
the parse layer never has to defend against shape errors — only against missing
content, which is expressed as empty strings and empty lists.

Numbers come back as **strings**. A float round-trip would quietly change
extracted figures, and every downstream comparison is done in ``Decimal``.
"""

from __future__ import annotations

from typing import Any

from deckscan.config import Settings

#: Section-presence flags the completeness rules consume. Key -> what it asserts.
CLAIM_FIELDS: dict[str, str] = {
    "has_cash_flow": "A cash flow projection or a cash balance by period is present.",
    "has_income_statement_detail": "COGS, gross margin, or an opex breakdown is present.",
    "has_balance_sheet": "A balance sheet or statement of assets and liabilities is present.",
    "has_assumptions": "Drivers, pricing, or conversion assumptions behind the plan are shown.",
    "has_sensitivity_case": "More than one scenario is presented (base/bear/bull, sensitivity).",
    "has_cap_table": "A cap table, prior round history, or outstanding instruments are shown.",
    "has_use_of_funds": "A line-item use of proceeds is present.",
    "has_pricing": "A price point, pricing model, or tier structure is disclosed.",
    "has_tam_methodology": "The market size figure has a bottom-up derivation shown.",
    "has_headcount_plan": "A headcount figure or hiring plan is present.",
    "claims_financial_rigor": (
        "The materials present themselves as containing financial substance: a "
        "projected revenue series, a financials/projections section, or a P&L."
    ),
}

_METHOD_ENUM = ["text", "table", "ocr", "cell"]


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        # Structured outputs require every property to be listed as required; the
        # system prompt is what makes unknown values come back empty.
        "required": list(properties),
    }


def extraction_schema(settings: Settings, narrative_fields: dict[str, str]) -> dict[str, Any]:
    """Build the extraction schema for one source document.

    ``narrative_fields`` comes from the one-pager template, so the fields Claude is
    asked for and the fields the document renders stay the same set by construction.
    """
    metric_names = sorted(settings.metric_aliases)

    return _object(
        {
            "narrative": _object(
                {field: _string(guidance) for field, guidance in narrative_fields.items()}
            ),
            "sector": _string("Sector in a few words, e.g. 'B2B SaaS'. Empty if unclear."),
            "stage_guess": _string(
                "Funding stage exactly as stated: pre-seed, seed, series a, series b, "
                "series c, growth. Empty if the source does not state one."
            ),
            "deck_date": _string(
                "As-of date of the materials, ISO 8601 (YYYY-MM-DD or YYYY-MM). Use a "
                "date printed in the source. Empty if none is printed."
            ),
            "periods": {
                "type": "array",
                "description": (
                    "Every period the financials cover, oldest first. A period is "
                    "'actual' only if the source labels it as historical/actual or it "
                    "is plainly a closed period being reported; otherwise 'projected'."
                ),
                "items": _object(
                    {
                        "label": _string("Period label as written, e.g. 'FY2024', 'Q3-2025'."),
                        "kind": {
                            "type": "string",
                            "enum": ["actual", "projected"],
                            "description": "Whether this period is historical or forecast.",
                        },
                        "locator": _string("Where it appears: 'p.11', 'slide 9', 'Model!C3'."),
                    }
                ),
            },
            "series": {
                "type": "array",
                "description": (
                    "Every by-period financial figure. One entry per (series, period). "
                    "Include revenue, opex, burn, headcount, customers, sales and "
                    "marketing spend, CAC and cash balance wherever they are given by "
                    "period. Empty array if the source has no by-period figures."
                ),
                "items": _object(
                    {
                        "name": {
                            "type": "string",
                            "enum": metric_names,
                            "description": "Canonical series name.",
                        },
                        "period": _string("Period label, matching one in 'periods'."),
                        "value": _string(
                            "The figure as a plain decimal number in base units - no "
                            "currency symbol, no thousands separators, no 'k'/'M' "
                            "suffix. $4.2M becomes 4200000. 65% becomes 0.65."
                        ),
                        "unit": _string("USD, %, months, x, people, or count."),
                        "locator": _string("Where it appears: 'p.11', 'slide 9', 'Model!C14'."),
                        "method": {
                            "type": "string",
                            "enum": _METHOD_ENUM,
                            "description": (
                                "How it was read: 'table' from a table, 'cell' from a "
                                "spreadsheet cell, 'text' from prose, 'ocr' from an "
                                "image with no text layer."
                            ),
                        },
                    }
                ),
            },
            "metrics": {
                "type": "array",
                "description": (
                    "Every standalone figure not tied to a period series - CAC, LTV, "
                    "gross margin, churn, retention, ARPU, runway, monthly burn, TAM, "
                    "SAM, SOM, raise amount, valuation, headcount. Record the SAME "
                    "metric more than once when the source states it in more than one "
                    "place, even if the values agree."
                ),
                "items": _object(
                    {
                        "name": {
                            "type": "string",
                            "enum": metric_names,
                            "description": "Canonical metric name.",
                        },
                        "value": _string("Plain decimal in base units, as for series."),
                        "unit": _string("USD, %, months, x, people, or count."),
                        "period": _string("Period it refers to, or empty if none."),
                        "snippet": _string("The short phrase it was read from."),
                        "locator": _string("Where it appears: 'p.4', 'slide 12', 'Model!B7'."),
                        "method": {
                            "type": "string",
                            "enum": _METHOD_ENUM,
                            "description": "How it was read.",
                        },
                    }
                ),
            },
            "claims": _object(
                {
                    field: {"type": "boolean", "description": desc}
                    for field, desc in CLAIM_FIELDS.items()
                }
            ),
        }
    )


SYSTEM_PROMPT = (
    "You are a financial data extractor for a venture capital deal team. You read "
    "investor materials and return only what they actually say.\n\n"
    "Rules you must follow:\n"
    "- Never infer, estimate, average, or compute a figure that is not written down. "
    "If a value is absent, return an empty string, and for booleans return false.\n"
    "- Never smooth over a contradiction. If the same metric appears twice with "
    "different values, record both entries with their own locators.\n"
    "- Convert every figure to base units: '$4.2M' is 4200000, '65%' is 0.65, "
    "'18 mo' is 18. Keep the sign. If a figure's scale is genuinely ambiguous "
    "(a bare '1.2' in a revenue row with no units anywhere), omit it rather than guess.\n"
    "- Label a period 'actual' only when the source presents it as historical or "
    "reported. Anything forward-looking is 'projected'. When a table mixes both, "
    "read the column headers carefully - the boundary between them matters more "
    "than any single number.\n"
    "- Locators must point at where you read the value: 'p.7' for a PDF page, "
    "'slide 12' for a slide, 'Sheet!C14' for a spreadsheet cell.\n"
    "- Narrative fields are quotes or tight paraphrases of the source, one to three "
    "sentences, no commentary of your own."
)


def user_prompt(source_label: str, body: str | None) -> str:
    """The instruction that accompanies the attached or inlined source."""
    head = (
        f"Extract the financial and narrative data from the {source_label} provided. "
        "Return empty values for anything it does not state."
    )
    if body is None:
        return head
    return f"{head}\n\n{body}"
