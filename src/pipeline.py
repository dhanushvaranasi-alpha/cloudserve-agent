"""
The pipeline: ingest -> classify -> retrieve -> route -> generate -> validate.

This is the one place that wires the six components together and writes
every decision to the log. `evaluation/harness.py` calls `run_ticket` (or
`run_batch`) once per ticket; `src/api/main.py` calls `run_ticket` per request.
"""
from __future__ import annotations

import logging
import time

from src.classify.classifier import classify
from src.config import SETTINGS
from src.generate.generator import generate
from src.llm_client import ChatClient
from src.persistence.decision_log import DecisionLog
from src.retrieve.retriever import Retriever
from src.route.router import route
from src.schemas import (
    ActionTaken,
    Channel,
    ClassificationResult,
    NormalizedTicket,
    PipelineResult,
    RetrievalResult,
    RoutingDecision,
    Urgency,
)
from src.validate.guardrails import run_guardrails

logger = logging.getLogger(__name__)


def run_ticket(
    raw: dict,
    *,
    client: ChatClient,
    retriever: Retriever,
    log: DecisionLog,
) -> PipelineResult:
    from src.ingest.ingest import normalize_ticket

    started = time.monotonic()
    try:
        ticket: NormalizedTicket = normalize_ticket(raw)
    except Exception as exc:  # noqa: BLE001
        # Ingest itself must never crash the run (A11). If even the minimal
        # normalize_ticket fallback somehow throws, log a synthetic error
        # result -- forced escalate, well-formed decision -- rather than
        # propagating and taking the whole unattended run down with it.
        ticket_id = str(raw.get("ticket_id", "UNKNOWN"))
        logger.exception("Ingest failed for %s", ticket_id)
        fallback_classification = ClassificationResult(
            intent="unclear_request", urgency=Urgency.MEDIUM, confidence=0.0,
            alternatives=[], used_fallback=True,
        )
        fallback_retrieval = RetrievalResult(passages=[], query="")
        fallback_routing = RoutingDecision(
            action=ActionTaken.ESCALATE,
            reason=f"Ingest failed ({exc}); escalated rather than dropped.",
            threshold_applied=SETTINGS.confidence_threshold,
            forced=True,
        )
        log.record(
            ticket_id=ticket_id,
            stage="classification",
            input_summary="(ingest failed)",
            action_taken="ingest_error",
            reason=str(exc),
        )
        return PipelineResult(
            ticket_id=ticket_id,
            channel=Channel.EMAIL,
            classification=fallback_classification,
            retrieval=fallback_retrieval,
            routing=fallback_routing,
            final_action=ActionTaken.ESCALATE,
            escalation_summary=f"Ingest error, escalated for manual handling: {exc}",
            error=str(exc),
            latency_seconds=time.monotonic() - started,
        )

    # --- classify ---
    classification = classify(ticket, client)
    log.record(
        ticket_id=ticket.ticket_id,
        stage="classification",
        input_summary=ticket.text[:300],
        model_name=SETTINGS.classifier_model,
        prediction_value=classification.intent,
        prediction_confidence=classification.confidence,
        alternatives=[a for a in classification.alternatives],
        action_taken="classified",
        reason=f"predicted intent={classification.intent} urgency={classification.urgency.value}"
               + (" (fallback used)" if classification.used_fallback else ""),
        prompt_version=SETTINGS.prompt_version_classify,
        requirement_ids=["FR-02", "FR-03"],
    )

    # --- retrieve ---
    retrieval = retriever.retrieve(ticket.text)
    log.record(
        ticket_id=ticket.ticket_id,
        stage="retrieval",
        input_summary=ticket.text[:300],
        sources_used=[{"doc_id": p.doc_id, "score": p.score} for p in retrieval.passages],
        action_taken="retrieved" if retrieval.has_grounding else "no_grounding_found",
        reason=f"{len(retrieval.passages)} passage(s) above relevance threshold",
        requirement_ids=["FR-04"],
    )

    # --- route ---
    routing = route(classification, retrieval)
    log.record(
        ticket_id=ticket.ticket_id,
        stage="routing",
        input_summary=ticket.text[:300],
        prediction_value=routing.action.value,
        threshold_applied=routing.threshold_applied,
        action_taken=routing.action.value,
        reason=routing.reason,
        requirement_ids=["FR-08"],
    )

    generation = None
    validation = None
    final_text_sent = None
    escalation_summary = None
    final_action = routing.action

    if routing.action == ActionTaken.AUTO_RESPOND:
        # --- generate ---
        generation = generate(ticket, retrieval, client)
        log.record(
            ticket_id=ticket.ticket_id,
            stage="generation",
            input_summary=ticket.text[:300],
            model_name=SETTINGS.model_name,
            prediction_value="declined" if generation.declined else "answered",
            sources_used=generation.citations,
            action_taken="declined" if generation.declined else "drafted",
            reason=generation.declined_reason or "grounded answer drafted",
            prompt_version=SETTINGS.prompt_version_generate,
            requirement_ids=["FR-05", "FR-06", "FR-07"],
        )

        if generation.declined:
            final_action = ActionTaken.ESCALATE
            escalation_summary = (
                f"Auto-generation declined to answer: {generation.declined_reason}. "
                f"Predicted intent: {classification.intent} (confidence {classification.confidence:.2f})."
            )
        else:
            # --- validate ---
            validation = run_guardrails(
                classification=classification,
                retrieval=retrieval,
                generation=generation,
                ticket_text=ticket.text,
            )
            log.record(
                ticket_id=ticket.ticket_id,
                stage="validation",
                input_summary=ticket.text[:300],
                action_taken="blocked" if validation.blocked else "passed",
                reason="; ".join(f"{f.name}={'pass' if f.passed else 'FAIL: ' + f.detail}" for f in validation.findings),
                guardrail_results={f.name: f.passed for f in validation.findings},
                requirement_ids=["FR-09"],
            )

            if validation.blocked:
                final_action = ActionTaken.ESCALATE
                failed = ", ".join(f.name for f in validation.blocking_findings)
                escalation_summary = (
                    f"Guardrail(s) blocked this response: {failed}. "
                    f"Predicted intent: {classification.intent}. Draft withheld from customer."
                )
            else:
                final_action = ActionTaken.AUTO_RESPOND
                final_text_sent = generation.answer_text
    else:
        escalation_summary = (
            f"{routing.reason} Predicted intent: {classification.intent} "
            f"(confidence {classification.confidence:.2f}). "
            f"Retrieved sources: {[p.doc_id for p in retrieval.passages]}."
        )

    return PipelineResult(
        ticket_id=ticket.ticket_id,
        channel=ticket.channel,
        classification=classification,
        retrieval=retrieval,
        routing=routing,
        generation=generation,
        validation=validation,
        final_action=final_action,
        final_text_sent=final_text_sent,
        escalation_summary=escalation_summary,
        latency_seconds=time.monotonic() - started,
    )
