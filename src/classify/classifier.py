"""
Classify: intent + urgency + calibrated confidence.

Build spec requirements this satisfies:
- Assigns intent + urgency to every ticket (A3)
- Attaches a numeric confidence between 0 and 1 (A3)
- Returns a defined fallback rather than raising when it cannot classify
- Records the alternatives it considered
"""
from __future__ import annotations

import json
import logging

from src.config import SETTINGS
from src.llm_client import ChatClient, ProviderError
from src.schemas import INTENT_CLASSES, ClassificationResult, NormalizedTicket, Urgency

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = f"""You are a support ticket classifier for CloudServe Solutions, a company
that sells cloud infrastructure and developer tooling.

Classify the ticket below into exactly one of these 22 intent categories:
{', '.join(INTENT_CLASSES)}.

If the ticket does not clearly fit any category, or is too vague to
classify, use "unclear_request". Do not invent a category outside this list.

Also assign an urgency of "high", "medium", or "low", based on business
impact described or implied in the ticket (e.g. production down = high,
a general how-to question = low).

Give a confidence score between 0.0 and 1.0 representing your genuine
estimate of the probability that your chosen intent is correct -- not how
confident you feel, but how often you believe a classification like this
one would be right if checked. Reserve above 0.85 for cases with little
ambiguity. List up to 2 alternative intents you considered with their own
confidence, if any were close.

Respond with JSON only, in this exact shape:
{{"intent": "<one of the 22 classes>", "urgency": "high|medium|low",
 "confidence": <float 0-1>,
 "alternatives": [{{"intent": "<class>", "confidence": <float>}}]}}
"""

FALLBACK_INTENT = "unclear_request"


def _fallback(reason: str) -> ClassificationResult:
    logger.warning("Classification fallback triggered: %s", reason)
    return ClassificationResult(
        intent=FALLBACK_INTENT,
        urgency=Urgency.MEDIUM,
        confidence=0.0,
        alternatives=[],
        used_fallback=True,
    )


def classify(ticket: NormalizedTicket, client: ChatClient) -> ClassificationResult:
    user_prompt = f"Channel: {ticket.channel.value}\nSubject: {ticket.original_subject}\nBody:\n{ticket.original_body}"

    try:
        raw = client.chat(
            model=SETTINGS.classifier_model,
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            temperature=0.0,  # deterministic: required for A5 (same input -> same decision)
            json_mode=True,
        )
    except ProviderError as exc:
        return _fallback(f"provider error: {exc}")

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _fallback(f"non-JSON response: {raw[:200]!r}")

    intent = data.get("intent")
    if intent not in INTENT_CLASSES:
        return _fallback(f"unrecognised intent from model: {intent!r}")

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    urgency_raw = str(data.get("urgency", "medium")).lower()
    try:
        urgency = Urgency(urgency_raw)
    except ValueError:
        urgency = Urgency.MEDIUM

    alternatives = data.get("alternatives") or []
    if not isinstance(alternatives, list):
        alternatives = []

    return ClassificationResult(
        intent=intent,
        urgency=urgency,
        confidence=confidence,
        alternatives=alternatives,
        used_fallback=False,
    )
