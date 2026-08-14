"""Number formatting shared by the rule findings and both renderers.

Presentation only — nothing here decides anything. It lives outside both layers
so the renderers can format a figure without importing the analysis layer.
"""

from __future__ import annotations

from decimal import Decimal

MONEY_UNITS = {"USD", "EUR", "GBP", "$"}


def fmt_money(value: Decimal | None, unit: str | None = "USD") -> str:
    """$4.2M / $850K / $1,200 — the scale an investor reads at a glance."""
    if value is None:
        return "not disclosed"
    if unit and unit not in MONEY_UNITS:
        return fmt_plain(value)
    magnitude = abs(value)
    sign = "-" if value < 0 else ""
    if magnitude >= 1_000_000_000:
        return f"{sign}${magnitude / Decimal(1_000_000_000):.1f}B"
    if magnitude >= 1_000_000:
        return f"{sign}${magnitude / Decimal(1_000_000):.1f}M"
    # Below $10K, round numbers to the dollar. Rounding $2,727 to "$3K" would blur
    # exactly the figures these rules fire on — cost per head, CAC, ARPU.
    if magnitude >= 10_000:
        return f"{sign}${magnitude / Decimal(1_000):.0f}K"
    return f"{sign}${magnitude:,.0f}"


def fmt_plain(value: Decimal | None) -> str:
    if value is None:
        return "not disclosed"
    if value == value.to_integral_value():
        return f"{value.to_integral_value():,}"
    return f"{value:,.1f}"


def fmt_multiple(value: Decimal | float | None) -> str:
    if value is None:
        return "?"
    return f"{Decimal(str(value)):.1f}"


def fmt_percent(value: Decimal | float | None, already_percent: bool = False) -> str:
    """0.65 -> '65'. Percent signs live in the templates, not here."""
    if value is None:
        return "?"
    number = Decimal(str(value))
    if not already_percent:
        number *= 100
    return f"{number:.0f}" if number == number.to_integral_value() else f"{number:.1f}"


def fmt_months(value: Decimal | float | None) -> str:
    if value is None:
        return "?"
    return f"{Decimal(str(value)):.0f}"


def fmt_metric(value: Decimal | None, unit: str | None) -> str:
    """Format a metric for a snapshot tile, choosing the shape from its unit."""
    if value is None:
        return ""
    if unit == "%":
        return f"{fmt_percent(value)}%"
    if unit == "x":
        return f"{fmt_multiple(value)}x"
    if unit == "months":
        return f"{fmt_months(value)} mo"
    if unit in {"people", "count"}:
        return fmt_plain(value)
    return fmt_money(value, unit)
