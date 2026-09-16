"""
Ingest: normalise tickets from all four channels into one internal shape.

Acceptance criterion A2. Failure mode to design against (Build Spec):
channel-specific quirks leaking into every downstream component. So this is
the only place that ever looks at `channel` to decide how to read the raw
text -- everything after this sees NormalizedTicket and nothing else.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from src.schemas import Channel, CustomerTier, LanguageFluency, NormalizedTicket, RawTicket

_WHITESPACE_RE = re.compile(r"\s+")


def _clean_text(text: str) -> str:
    if not text:
        return ""
    # Strip control characters but keep normal punctuation/unicode (non-fluent
    # English tickets and non-Latin scripts must survive this untouched).
    text = "".join(ch for ch in text if ch == "\n" or ch.isprintable())
    return _WHITESPACE_RE.sub(" ", text).strip()


def _parse_received_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def normalize_ticket(raw: dict) -> NormalizedTicket:
    """Normalise one raw ticket dict (as loaded from a JSON ticket file).

    Handles missing fields, unusual characters and empty bodies without
    raising -- a malformed ticket becomes a NormalizedTicket with empty/
    default fields rather than crashing the run (required for A11 and the
    "process the full set, nobody intervenes" requirement in A9).
    """
    ticket_id = str(raw.get("ticket_id") or f"UNKNOWN-{id(raw)}")

    channel_raw = str(raw.get("channel") or "email").lower()
    try:
        channel = Channel(channel_raw)
    except ValueError:
        channel = Channel.EMAIL  # safest default: treat unknown channels as email

    subject = _clean_text(str(raw.get("subject") or ""))
    body = _clean_text(str(raw.get("body") or ""))

    # docs_comment / chat / forum tickets are often subject-less by design
    # (see Dataset_Guide: "empty string for chat tickets"). Build the text
    # every downstream component reads from whatever is actually present.
    text = f"{subject}\n\n{body}".strip() if subject else body
    if not text:
        text = "(empty ticket body)"

    tier_raw = raw.get("customer_tier")
    try:
        tier = CustomerTier(tier_raw) if tier_raw else CustomerTier.STANDARD
    except ValueError:
        tier = CustomerTier.STANDARD

    fluency_raw = raw.get("language_fluency")
    try:
        fluency = LanguageFluency(fluency_raw) if fluency_raw else LanguageFluency.FLUENT
    except ValueError:
        fluency = LanguageFluency.FLUENT

    return NormalizedTicket(
        ticket_id=ticket_id,
        channel=channel,
        original_subject=subject,
        original_body=body,
        text=text,
        received_at=_parse_received_at(raw.get("received_at")),
        customer_tier=tier,
        customer_region=str(raw.get("customer_region") or "unknown"),
        language_fluency=fluency,
    )


def normalize_batch(raw_tickets: list[dict]) -> list[NormalizedTicket]:
    normalized = []
    for raw in raw_tickets:
        try:
            normalized.append(normalize_ticket(raw))
        except Exception:  # noqa: BLE001 -- never let one bad record kill the run
            # Fall back to a minimal empty-body ticket rather than dropping it
            # silently: A9 requires every ticket to produce an outcome.
            normalized.append(
                NormalizedTicket(
                    ticket_id=str(raw.get("ticket_id", "UNKNOWN")),
                    channel=Channel.EMAIL,
                    original_subject="",
                    original_body="",
                    text="(unparseable ticket)",
                    received_at=datetime.now(timezone.utc),
                )
            )
    return normalized
