"""The Claude call that turns prepared source content into structured data.

One streamed, structured-output request per source. Structured outputs guarantee
the response matches :func:`deckscan.extract.schema.extraction_schema`, so the
caller only has to handle *missing* content, never malformed content.
"""

from __future__ import annotations

import json
import os
from typing import Any

from deckscan.config import Settings
from deckscan.extract.schema import SYSTEM_PROMPT, extraction_schema, user_prompt
from deckscan.extract.source import SourceContent

MODEL = "claude-opus-5"

#: Claude Opus 5's safety classifiers can decline a request (HTTP 200 with
#: stop_reason "refusal"). Server-side fallbacks re-run a declined request on
#: Anthropic's recommended substitute inside the same call, routed by category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: Extraction is literal reading, not reasoning - depth buys little and costs latency.
EFFORT = "medium"
MAX_TOKENS = 16_000


class ExtractionError(RuntimeError):
    """Raised when a source could not be extracted. Recorded as a gap, never fatal."""


def api_key_present() -> bool:
    """Whether an API key is configured. Used by the CLI and the web health check."""
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def get_client() -> Any:
    """Build an Anthropic client, failing clearly if the API key is absent."""
    if not api_key_present():
        raise ExtractionError(
            "ANTHROPIC_API_KEY is not set. Create a key at "
            "https://console.anthropic.com/settings/keys and put it in your .env file."
        )
    import anthropic

    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())


def extract(
    content: SourceContent,
    settings: Settings,
    narrative_fields: dict[str, str],
) -> dict[str, Any]:
    """Run one extraction request and return the parsed payload.

    Raises:
        ExtractionError: on a missing key, an API failure, a refusal, or a
            truncated response. Callers degrade this to a recorded gap.
    """
    if content.is_empty:
        raise ExtractionError(f"Nothing readable was found in the {content.label}.")

    client = get_client()
    schema = extraction_schema(settings, narrative_fields)

    blocks: list[dict[str, Any]] = list(content.blocks)
    blocks.append({"type": "text", "text": user_prompt(content.label, content.inline_text)})

    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": blocks}],
        ) as stream:
            message = stream.get_final_message()
    except ExtractionError:
        raise
    except Exception as exc:  # surface the raw API error to the caller
        raise ExtractionError(f"Claude API error while reading the {content.label}: {exc}") from exc

    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) or "unspecified"
        raise ExtractionError(
            f"Claude declined to read the {content.label} (refusal category: {category}). "
            "The fallback model declined it as well."
        )
    if message.stop_reason == "max_tokens":
        raise ExtractionError(
            f"The response for the {content.label} was truncated at {MAX_TOKENS} output "
            "tokens. Split the source or raise MAX_TOKENS."
        )

    text = next((b.text for b in message.content if b.type == "text"), "").strip()
    if not text:
        raise ExtractionError(f"Claude returned an empty response for the {content.label}.")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Could not parse the {content.label} response: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExtractionError(f"The {content.label} response was not a JSON object.")
    return payload
