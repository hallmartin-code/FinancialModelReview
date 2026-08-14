"""Run every rule family, aggregate, dedupe, and score.

Rules are pure functions of ``(DeckAnalysis, Settings) -> RuleResult``. The
engine never reads a file and never formats a number for display; it orders
results deterministically and attaches the score.
"""

from __future__ import annotations

from collections.abc import Callable

from deckscan.analyze.rules import burn, completeness, consistency, growth, unit_economics
from deckscan.analyze.rules.unit_economics import payback_ceiling
from deckscan.analyze.scoring import grounding_score
from deckscan.config import Settings
from deckscan.models import DeckAnalysis, Flag, Gap, RuleResult, sort_flags, sort_gaps

Rule = Callable[[DeckAnalysis, Settings], RuleResult]

#: Families run in a fixed order so a partial failure is reproducible.
FAMILIES: tuple[tuple[str, Rule], ...] = (
    ("growth", growth.evaluate),
    ("burn", burn.evaluate),
    ("unit_economics", unit_economics.evaluate),
    ("completeness", completeness.evaluate),
    ("consistency", consistency.evaluate),
)


def _dedupe_flags(flags: list[Flag]) -> list[Flag]:
    """Drop exact ``(code, first locator)`` repeats only.

    Several growth rules firing on one series is intended and informative, so
    nothing else is collapsed.
    """
    seen: set[tuple[str, str]] = set()
    out: list[Flag] = []
    for flag in flags:
        key = (flag.code, flag.first_locator)
        if key in seen:
            continue
        seen.add(key)
        out.append(flag)
    return out


def _dedupe_gaps(gaps: list[Gap]) -> list[Gap]:
    seen: set[str] = set()
    out: list[Gap] = []
    for gap in gaps:
        if gap.field in seen:
            continue
        seen.add(gap.field)
        out.append(gap)
    return out


def run(analysis: DeckAnalysis, settings: Settings) -> DeckAnalysis:
    """Evaluate every rule against ``analysis`` and return it, populated.

    A rule that raises is contained: its family is skipped and the failure is
    recorded in methodology, because a malformed deck must never take the run down.
    """
    flags: list[Flag] = []
    gaps: list[Gap] = []

    for name, rule in FAMILIES:
        try:
            result = rule(analysis, settings)
        except Exception as exc:  # a bad extraction must not lose the whole report
            analysis.methodology.append(
                f"The {name} rules could not be evaluated ({type(exc).__name__}: {exc})."
            )
            continue
        flags.extend(result.flags)
        gaps.extend(result.gaps)

    analysis.flags = sort_flags(_dedupe_flags(flags))
    analysis.gaps = sort_gaps(_dedupe_gaps(gaps))
    analysis.grounding_score = grounding_score(analysis, settings)

    _, label, assumed = payback_ceiling(analysis, settings)
    if assumed and analysis.primary("cac_payback_months") is not None:
        ceiling = settings.thresholds.unit_economics.max_cac_payback_months["default"]
        analysis.methodology.append(
            f"Sector could not be classified as B2B or B2C, so the default "
            f"{ceiling:.0f}-month CAC payback ceiling was applied ({label})."
        )

    return analysis
