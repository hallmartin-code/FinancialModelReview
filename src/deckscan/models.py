"""Core data models for deckscan.

Every value that reaches the one-pager carries provenance back to a location in
the input. Models here are deliberately permissive about *missing* data (that is
recorded as a ``Gap``) and strict about *unsupported* data (a ``Flag`` without
evidence or without a number in its finding is a bug, and is rejected here).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["critical", "high", "medium", "info"]

#: Lower rank sorts first. Used for every deterministic ordering in the pipeline.
SEVERITY_RANK: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "info": 3}

#: Codes that assert the *absence* of something and therefore may carry no
#: evidence and no numeric token. Populated from ``rules.yaml`` at config load
#: time (see :func:`deckscan.config.load_config`); the default below matches the
#: shipped configuration so that models remain usable without a loaded config.
_EVIDENCE_EXEMPT_CODES: frozenset[str] = frozenset(
    {
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
        "NO_ACTUALS",
        "NO_HEADCOUNT_PLAN",
    }
)

_NUMERIC_TOKEN = re.compile(r"\d")


def set_evidence_exempt_codes(codes: frozenset[str]) -> None:
    """Install the evidence-exemption list from configuration.

    Called by the config loader so that the exemption list lives in
    ``rules.yaml`` rather than in code, while keeping ``Flag`` validation a plain
    pydantic validator that rules can trigger by constructing ``Flag(...)``.
    """
    global _EVIDENCE_EXEMPT_CODES
    _EVIDENCE_EXEMPT_CODES = codes


def evidence_exempt_codes() -> frozenset[str]:
    """Return the currently installed evidence-exemption list."""
    return _EVIDENCE_EXEMPT_CODES


class Provenance(BaseModel):
    """Where a value came from in the source material."""

    model_config = ConfigDict(frozen=True)

    source: Literal["deck", "model"]
    locator: str
    """Human-readable origin: ``"p.7"``, ``"slide 12"``, ``"Financials!C14"``."""
    snippet: str | None = None
    method: Literal["text", "table", "ocr", "cell"]
    confidence: float = Field(ge=0.0, le=1.0)


class Metric(BaseModel):
    """A single named figure extracted from one location."""

    name: str
    """Canonical key, e.g. ``"cac"``."""
    value: Decimal | None = None
    unit: str | None = None
    """``"USD"``, ``"%"``, ``"months"``, ``"x"``, ``"people"``."""
    period: str | None = None
    """``"FY2026"``, ``"Q3-2025"``, ``"TTM"``."""
    provenance: list[Provenance] = Field(default_factory=list)


class Flag(BaseModel):
    """A fired red-flag rule, always quoting the numbers it fired on."""

    code: str
    severity: Severity
    title: str
    finding: str
    """What the data shows, with the actual figures in it."""
    why_it_matters: str
    ask: str
    """The exact question to put to the founder."""
    evidence: list[Provenance] = Field(default_factory=list)
    load_bearing: bool = False
    """True when the evidence touches the raise ask or the terminal-year revenue."""

    @model_validator(mode="after")
    def _require_grounding(self) -> Flag:
        if self.code in _EVIDENCE_EXEMPT_CODES:
            return self
        if not self.evidence:
            raise ValueError(f"flag {self.code} has no evidence and is not evidence-exempt")
        if not _NUMERIC_TOKEN.search(self.finding):
            raise ValueError(
                f"flag {self.code} finding contains no numeric token: {self.finding!r}"
            )
        return self

    @property
    def first_locator(self) -> str:
        return self.evidence[0].locator if self.evidence else ""


class Gap(BaseModel):
    """Something the analysis needed and could not find."""

    field: str
    label: str
    severity: Severity = "medium"
    ask: str
    load_bearing: bool = False


class RuleResult(BaseModel):
    """Return type of every rule: rules may report absence as well as findings."""

    flags: list[Flag] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)


class DeckAnalysis(BaseModel):
    """The complete analysis payload; both one-pagers are renderings of this."""

    company: str | None = None
    tagline: str | None = None
    sector: str | None = None
    stage_guess: str | None = None
    deck_date: str | None = None
    """As-of date for the deck, from document metadata or an in-deck date string."""
    raise_ask: Metric | None = None
    narrative: dict[str, str] = Field(default_factory=dict)
    """Template ``analysis_fields`` -> extracted text. ``""`` means not found, and the
    renderer omits that section rather than inventing one."""
    actual_periods: list[str] = Field(default_factory=list)
    """Period labels the source identifies as historical, in chronological order."""
    projected_periods: list[str] = Field(default_factory=list)
    """Period labels the source identifies as projected, in chronological order."""
    claims: dict[str, bool] = Field(default_factory=dict)
    """Presence flags for whole sections (cash flow, cap table, ...), used by the
    completeness rules."""
    metrics: dict[str, list[Metric]] = Field(default_factory=dict)
    """One list per canonical name. Conflicting values must both survive so that
    the consistency rules can compare them."""
    primary_metric: dict[str, int] = Field(default_factory=dict)
    """Canonical name -> index into ``metrics[name]`` of the authoritative value."""
    series: dict[str, list[tuple[str, Decimal]]] = Field(default_factory=dict)
    flags: list[Flag] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    grounding_score: int = Field(default=100, ge=0, le=100)
    methodology: list[str] = Field(default_factory=list)

    def primary(self, name: str) -> Metric | None:
        """Return the authoritative ``Metric`` for ``name``, if one was extracted."""
        candidates = self.metrics.get(name)
        if not candidates:
            return None
        index = self.primary_metric.get(name, 0)
        if index < 0 or index >= len(candidates):
            return candidates[0]
        return candidates[index]

    def value_at(self, series: str, period: str) -> Decimal | None:
        """Value of ``series`` in ``period``, or None if that pair was not extracted."""
        for label, value in self.series.get(series, []):
            if label == period:
                return value
        return None

    def counts_by_severity(self) -> dict[str, int]:
        """Flag counts keyed by severity, including zero entries, ranked order."""
        return {sev: sum(1 for f in self.flags if f.severity == sev) for sev in SEVERITY_RANK}


def sort_flags(flags: list[Flag]) -> list[Flag]:
    """Deterministic flag order: severity, then code, then first locator."""
    return sorted(flags, key=lambda f: (SEVERITY_RANK[f.severity], f.code, f.first_locator))


def sort_gaps(gaps: list[Gap]) -> list[Gap]:
    """Deterministic gap order: severity, then field."""
    return sorted(gaps, key=lambda g: (SEVERITY_RANK[g.severity], g.field))


def canonical_json(analysis: DeckAnalysis) -> str:
    """Serialize an analysis to byte-stable JSON.

    Dict keys are sorted and ``Decimal`` values are emitted as strings, so two
    runs over the same non-OCR input produce identical bytes. Nothing
    time-dependent, path-dependent or random is part of the payload.
    """
    payload: dict[str, Any] = analysis.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
