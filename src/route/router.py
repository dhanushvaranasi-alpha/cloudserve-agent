"""
Route: decide auto_respond vs escalate.

Build spec requirements (A5):
- Deterministic: same input -> same decision (no randomness here, and the
  classifier is called at temperature=0, so re-running an identical ticket
  reproduces the same confidence and therefore the same route)
- Records the reason in language a support manager could read
- Uses a threshold set from data, not guessed

Threshold: CONFIDENCE_THRESHOLD in .env, default 0.80. That number is the
pack's illustrative starting point; evaluation/harness.py's calibration
table is what should actually justify (or move) it once real classifier
runs exist -- see docs/architecture.md "Threshold selection".
"""
from __future__ import annotations

from src.config import SETTINGS
from src.schemas import (
    ALWAYS_ESCALATE_INTENTS,
    ActionTaken,
    ClassificationResult,
    RetrievalResult,
    RoutingDecision,
)


def route(classification: ClassificationResult, retrieval: RetrievalResult,
          threshold: float | None = None) -> RoutingDecision:
    threshold = SETTINGS.confidence_threshold if threshold is None else threshold

    if classification.used_fallback:
        return RoutingDecision(
            action=ActionTaken.ESCALATE,
            reason="Classification fell back (provider error or unparseable response); "
                   "a missing confidence score is treated as a low one, not a high one.",
            threshold_applied=threshold,
            forced=True,
        )

    if classification.intent in ALWAYS_ESCALATE_INTENTS:
        return RoutingDecision(
            action=ActionTaken.ESCALATE,
            reason=f"Intent '{classification.intent}' is on the always-escalate list "
                   "(security, compliance, feature requests and unclear requests are never "
                   "auto-answered, regardless of confidence -- backed by discovery evidence "
                   "where these classes were escalated 100% of the time historically).",
            threshold_applied=threshold,
            forced=True,
        )

    if not retrieval.has_grounding:
        return RoutingDecision(
            action=ActionTaken.ESCALATE,
            reason="No documentation passage cleared the relevance threshold; nothing to "
                   "ground an answer in, so this escalates rather than answering ungrounded.",
            threshold_applied=threshold,
            forced=True,
        )

    if classification.confidence >= threshold:
        return RoutingDecision(
            action=ActionTaken.AUTO_RESPOND,
            reason=f"Classification confidence {classification.confidence:.2f} meets the "
                   f"{threshold:.2f} threshold and relevant documentation was found.",
            threshold_applied=threshold,
            forced=False,
        )

    return RoutingDecision(
        action=ActionTaken.ESCALATE,
        reason=f"Classification confidence {classification.confidence:.2f} is below the "
               f"{threshold:.2f} threshold.",
        threshold_applied=threshold,
        forced=False,
    )
