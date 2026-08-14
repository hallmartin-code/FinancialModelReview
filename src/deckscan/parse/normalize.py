"""Turn extracted strings into ``Decimal`` values.

The extractor is instructed to return plain decimals in base units, so this is a
defensive normalizer rather than a parser: it strips the currency symbols,
separators, and scale suffixes that occasionally survive, and returns ``None``
for anything it cannot read rather than guessing a magnitude.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from deckscan.config import Settings

_CLEAN = re.compile(r"[,\s_]")
_NUMBER = re.compile(r"^-?\(?\$?\s*-?\d*\.?\d+\)?$")


def to_decimal(raw: str | None, settings: Settings | None = None) -> Decimal | None:
    """Parse one extracted figure. Returns None when the text is not a number."""
    if raw is None:
        return None
    text = _CLEAN.sub("", str(raw)).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]

    multiplier = Decimal(1)
    percent = text.endswith("%")
    if percent:
        text = text[:-1]

    if settings is not None:
        lowered = text.lower()
        for symbol in settings.normalization.currency_symbols:
            if lowered.startswith(symbol.lower()):
                text = text[len(symbol) :]
                lowered = text.lower()
                break
        for suffix, factor in sorted(
            settings.normalization.scale_multipliers.items(), key=lambda kv: -len(kv[0])
        ):
            if lowered.endswith(suffix) and len(lowered) > len(suffix):
                text = text[: -len(suffix)]
                multiplier = Decimal(str(factor))
                break
    text = text.lstrip("$£€").strip()

    if not _NUMBER.match(text.replace("$", "")):
        return None
    try:
        value = Decimal(text.replace("$", ""))
    except InvalidOperation:
        return None

    value *= multiplier
    if percent:
        value /= Decimal(100)
    return -value if negative else value


def clean_text(raw: str | None) -> str:
    """Collapse whitespace; empty string means 'the source did not say'."""
    if not raw:
        return ""
    return " ".join(str(raw).split()).strip()
