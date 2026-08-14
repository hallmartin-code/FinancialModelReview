"""Typed loader for ``config/rules.yaml``.

The packaged YAML is the single source of truth for thresholds, keyword lists,
severities and user-visible text. A file passed to ``--config`` is an override:
it is deep-merged over the packaged defaults, so it only needs the keys it
changes.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from deckscan.models import Severity, set_evidence_exempt_codes

# The data directory ``deckscan/config/`` is not an importable package - the
# module ``deckscan.config`` (this file) owns that name - so the resource is
# addressed relative to the ``deckscan`` package instead.
PACKAGE_CONFIG = ("deckscan", "config/rules.yaml")

Band = Literal["well_grounded", "needs_grounding", "weakly_grounded"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExtractionConfig(_Frozen):
    min_chars_per_page: int
    ocr_dpi: int
    ocr_lang: str
    min_pptx_image_area_px: int
    confidence_by_method: dict[str, float]


class NormalizationConfig(_Frozen):
    currency_symbols: dict[str, str]
    scale_multipliers: dict[str, float]
    bare_number_ambiguity_ceiling: float


class KeywordsConfig(_Frozen):
    financials_section_keywords: list[str]
    cash_flow_keywords: list[str]
    income_statement_detail_keywords: list[str]
    balance_sheet_keywords: list[str]
    assumptions_keywords: list[str]
    sensitivity_keywords: list[str]
    cap_table_keywords: list[str]
    use_of_funds_keywords: list[str]
    pricing_keywords: list[str]
    tam_methodology_keywords: list[str]
    headcount_keywords: list[str]
    actual_period_keywords: list[str]
    projected_period_keywords: list[str]
    b2b_keywords: list[str]
    b2c_keywords: list[str]


class GrowthThresholds(_Frozen):
    hockey_stick_multiple: float
    hockey_stick_ratio_vs_actual: float
    hockey_stick_critical_multiple: float
    hockey_stick_critical_base_usd: float
    growth_discontinuity_ratio: float
    flat_actual_multiple_max: float
    flat_then_spike_multiple: float
    market_share_ceiling: float


class BurnThresholds(_Frozen):
    min_monthly_cost_per_head: float
    headcount_growth_floor: float
    opex_growth_floor: float
    burn_flat_revenue_multiple: float
    burn_flat_opex_multiple: float
    runway_mismatch_tolerance: float
    min_runway_months: float


class UnitEconomicsThresholds(_Frozen):
    ltv_cac_ceiling: float
    ltv_cac_ceiling_by_stage: dict[str, float]
    ltv_cac_critical: float
    ltv_cac_underwater: float
    ltv_revenue_match_tolerance: float
    ltv_margin_check_gross_margin_max: float
    max_cac_payback_months: dict[str, float]
    cac_static_tolerance: float
    organic_growth_revenue_multiple: float
    organic_growth_sm_multiple: float


class ConsistencyThresholds(_Frozen):
    value_tolerance: float
    arithmetic_tolerance: float
    stale_data_months: int


class CompletenessThresholds(_Frozen):
    min_projected_years: int


class Thresholds(_Frozen):
    growth: GrowthThresholds
    burn: BurnThresholds
    unit_economics: UnitEconomicsThresholds
    consistency: ConsistencyThresholds
    completeness: CompletenessThresholds


class ScoringBands(_Frozen):
    well_grounded_min: int
    needs_grounding_min: int


class ScoringConfig(_Frozen):
    start: int
    flag_penalty: dict[str, int]
    gap_penalty: dict[str, int]
    provenance_bonus_max: int
    bands: ScoringBands
    band_labels: dict[str, str]


class RenderConfig(_Frozen):
    max_flags: int
    max_grounding_items: int
    max_metric_tiles: int
    min_chart_periods: int
    not_disclosed_label: str
    overflow_label: str
    disclaimer: str
    severity_colors: dict[str, str]
    text_color: str
    muted_color: str
    rule_color: str
    background_color: str
    font_family: str
    font_size_title: int
    font_size_section: int
    font_size_body: int
    font_size_small: int


class RuleText(_Frozen):
    """Everything user-visible about one flag code."""

    enabled: bool = True
    severity: Severity
    critical_severity: Severity | None = None
    title: str
    finding: str
    """Format template; rules fill it with the actual extracted numbers."""
    why_it_matters: str
    ask: str


class GapText(_Frozen):
    severity: Severity
    label: str
    ask: str


class Settings(BaseSettings):
    """The whole of ``rules.yaml``, typed."""

    model_config = SettingsConfigDict(
        env_prefix="DECKSCAN_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    version: int
    extraction: ExtractionConfig
    normalization: NormalizationConfig
    metric_aliases: dict[str, list[str]]
    headline_metrics: list[str]
    keywords: KeywordsConfig
    stage_keywords: dict[str, list[str]]
    sector_keywords: dict[str, list[str]]
    thresholds: Thresholds
    scoring: ScoringConfig
    render: RenderConfig
    evidence_exempt_codes: list[str] = Field(default_factory=list)
    rule_text: dict[str, RuleText]
    gap_text: dict[str, GapText]

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # deep_merge=True makes a --config override a partial patch over the
        # packaged defaults rather than a wholesale replacement of top-level keys.
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, deep_merge=True),
        )

    # -- convenience accessors -------------------------------------------------

    def rule(self, code: str) -> RuleText:
        try:
            return self.rule_text[code]
        except KeyError as exc:  # pragma: no cover - configuration error
            raise KeyError(f"no rule_text entry for flag code {code!r} in rules.yaml") from exc

    def gap(self, field: str) -> GapText:
        try:
            return self.gap_text[field]
        except KeyError as exc:  # pragma: no cover - configuration error
            raise KeyError(f"no gap_text entry for gap field {field!r} in rules.yaml") from exc

    def severity_color(self, severity: Severity) -> str:
        return self.render.severity_colors[severity]

    def band_label(self, score: int) -> str:
        if score >= self.scoring.bands.well_grounded_min:
            return self.scoring.band_labels["well_grounded"]
        if score >= self.scoring.bands.needs_grounding_min:
            return self.scoring.band_labels["needs_grounding"]
        return self.scoring.band_labels["weakly_grounded"]

    def band_key(self, score: int) -> Band:
        if score >= self.scoring.bands.well_grounded_min:
            return "well_grounded"
        if score >= self.scoring.bands.needs_grounding_min:
            return "needs_grounding"
        return "weakly_grounded"


def packaged_config() -> Traversable:
    """The packaged ``rules.yaml``, addressed without assuming a filesystem install."""
    return resources.files(PACKAGE_CONFIG[0]).joinpath(PACKAGE_CONFIG[1])


@lru_cache(maxsize=8)
def _load(override: str | None) -> Settings:
    files: list[Traversable | Path] = [packaged_config()]
    if override is not None:
        files.append(Path(override))
    # Later files win, deep-merged over earlier ones.
    Settings.model_config["yaml_file"] = files
    return Settings()


def load_config(path: Path | str | None = None) -> Settings:
    """Load the packaged configuration, optionally deep-merged with an override.

    Also installs the evidence-exemption list onto the models module so that the
    ``Flag`` "no flag without evidence" validator stays configuration-driven.
    """
    override = str(Path(path).resolve()) if path is not None else None
    settings = _load(override)
    set_evidence_exempt_codes(frozenset(settings.evidence_exempt_codes))
    return settings


def clear_config_cache() -> None:
    """Drop the cached settings. Used by tests that write override files."""
    _load.cache_clear()
