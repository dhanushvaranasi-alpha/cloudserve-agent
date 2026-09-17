"""
The pipeline: ingest -> classify -> retrieve -> route -> generate -> validate.

This is the one place that wires the six components together and writes
every decision to the log. `evaluation/harness.py` calls `run_ticket` (or
`run_batch`) once per ticket; `src/api/main.py` calls `run_ticket` per request.

LangGraph migration note: orchestration now goes through a
langgraph.graph.StateGraph instead of the plain-Python if/else control flow
this module used before. Every component function (classify, retrieve,
route, generate, run_guardrails) and every decision-log call is unchanged --
only how they're wired together moved, into one node per stage plus
conditional edges that encode the same three early-exit paths the old code
had: an always-escalate / low-confidence / no-grounding routing decision
skips generate() and validate() entirely, a decline in generate() skips
validate(), and a guardrail block downgrades auto_respond to escalate. The
graph is built once at import time (`_GRAPH`) and reused across calls;
per-ticket dependencies (the chat client, retriever and decision log) travel
through the graph state rather than as node closures, since a compiled
LangGraph graph's nodes only see state.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

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
    GenerationResult,
    NormalizedTicket,
    PipelineResult,
    RetrievalResult,
    RoutingDecision,
    Urgency,
    ValidationResult,
)
from src.validate.guardrails import run_guardrails

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    # Inputs / dependencies, set once before graph.invoke() and read-only
    # from every node's point of view.
    raw: dict
    client: ChatClient
    retriever: Retriever
    log: DecisionLog
    started: float

    # Populated by ingest_node.
    ticket_id: str
    channel: Channel
    ticket: NormalizedTicket
    ingest_failed: bool

    # Populated by later nodes.
    classification: ClassificationResult
    retrieval: RetrievalResult
    routing: RoutingDecision
    generation: Optional[GenerationResult]
    validation: Optional[ValidationResult]

    # Final result fields.
    final_action: ActionTaken
    final_text_sent: Optional[str]
    escalation_summary: Optional[str]
    error: Optional[str]


def _ingest_node(state: PipelineState) -> dict:
    from src.ingest.ingest import normalize_ticket

    raw = state["raw"]
    log = state["log"]
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
        return {
            "ingest_failed": True,
            "ticket_id": ticket_id,
            "channel": Channel.EMAIL,
            "classification": fallback_classification,
            "retrieval": fallback_retrieval,
            "routing": fallback_routing,
            "generation": None,
            "validation": None,
            "final_action": ActionTaken.ESCALATE,
            "final_text_sent": None,
            "escalation_summary": f"Ingest error, escalated for manual handling: {exc}",
            "error": str(exc),
        }

    return {
        "ingest_failed": False,
        "ticket": ticket,
        "ticket_id": ticket.ticket_id,
        "channel": ticket.channel,
    }


def _classify_node(state: PipelineState) -> dict:
    ticket = state["ticket"]
    log = state["log"]
    classification = classify(ticket, state["client"])
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
    return {"classification": classification}


def _retrieve_node(state: PipelineState) -> dict:
    ticket = state["ticket"]
    log = state["log"]
    retrieval = state["retriever"].retrieve(ticket.text)
    log.record(
        ticket_id=ticket.ticket_id,
        stage="retrieval",
        input_summary=ticket.text[:300],
        sources_used=[{"doc_id": p.doc_id, "score": p.score} for p in retrieval.passages],
        action_taken="retrieved" if retrieval.has_grounding else "no_grounding_found",
        reason=f"{len(retrieval.passages)} passage(s) above relevance threshold",
        requirement_ids=["FR-04"],
    )
    return {"retrieval": retrieval}


def _route_node(state: PipelineState) -> dict:
    ticket = state["ticket"]
    log = state["log"]
    routing = route(state["classification"], state["retrieval"])
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
    return {"routing": routing}


def _after_route(state: PipelineState) -> str:
    return "generate" if state["routing"].action == ActionTaken.AUTO_RESPOND else "finalize_routed_escalate"


def _finalize_routed_escalate_node(state: PipelineState) -> dict:
    routing = state["routing"]
    classification = state["classification"]
    retrieval = state["retrieval"]
    escalation_summary = (
        f"{routing.reason} Predicted intent: {classification.intent} "
        f"(confidence {classification.confidence:.2f}). "
        f"Retrieved sources: {[p.doc_id for p in retrieval.passages]}."
    )
    return {
        "generation": None,
        "validation": None,
        "final_action": routing.action,
        "final_text_sent": None,
        "escalation_summary": escalation_summary,
    }


def _generate_node(state: PipelineState) -> dict:
    ticket = state["ticket"]
    log = state["log"]
    generation = generate(ticket, state["retrieval"], state["client"])
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
    return {"generation": generation}


def _after_generate(state: PipelineState) -> str:
    return "finalize_declined" if state["generation"].declined else "validate"


def _finalize_declined_node(state: PipelineState) -> dict:
    generation = state["generation"]
    classification = state["classification"]
    escalation_summary = (
        f"Auto-generation declined to answer: {generation.declined_reason}. "
        f"Predicted intent: {classification.intent} (confidence {classification.confidence:.2f})."
    )
    return {
        "validation": None,
        "final_action": ActionTaken.ESCALATE,
        "final_text_sent": None,
        "escalation_summary": escalation_summary,
    }


def _validate_node(state: PipelineState) -> dict:
    ticket = state["ticket"]
    log = state["log"]
    validation = run_guardrails(
        classification=state["classification"],
        retrieval=state["retrieval"],
        generation=state["generation"],
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
    return {"validation": validation}


def _after_validate(state: PipelineState) -> str:
    return "finalize_blocked" if state["validation"].blocked else "finalize_auto_respond"


def _finalize_blocked_node(state: PipelineState) -> dict:
    validation = state["validation"]
    classification = state["classification"]
    failed = ", ".join(f.name for f in validation.blocking_findings)
    escalation_summary = (
        f"Guardrail(s) blocked this response: {failed}. "
        f"Predicted intent: {classification.intent}. Draft withheld from customer."
    )
    return {
        "final_action": ActionTaken.ESCALATE,
        "final_text_sent": None,
        "escalation_summary": escalation_summary,
    }


def _finalize_auto_respond_node(state: PipelineState) -> dict:
    return {
        "final_action": ActionTaken.AUTO_RESPOND,
        "final_text_sent": state["generation"].answer_text,
        "escalation_summary": None,
    }


def _build_graph():
    builder = StateGraph(PipelineState)
    builder.add_node("ingest", _ingest_node)
    builder.add_node("classify", _classify_node)
    builder.add_node("retrieve", _retrieve_node)
    builder.add_node("route", _route_node)
    builder.add_node("finalize_routed_escalate", _finalize_routed_escalate_node)
    builder.add_node("generate", _generate_node)
    builder.add_node("finalize_declined", _finalize_declined_node)
    builder.add_node("validate", _validate_node)
    builder.add_node("finalize_blocked", _finalize_blocked_node)
    builder.add_node("finalize_auto_respond", _finalize_auto_respond_node)

    builder.add_edge(START, "ingest")
    builder.add_conditional_edges(
        "ingest", lambda s: END if s["ingest_failed"] else "classify", {"classify": "classify", END: END}
    )
    builder.add_edge("classify", "retrieve")
    builder.add_edge("retrieve", "route")
    builder.add_conditional_edges(
        "route", _after_route, {"generate": "generate", "finalize_routed_escalate": "finalize_routed_escalate"}
    )
    builder.add_edge("finalize_routed_escalate", END)
    builder.add_conditional_edges(
        "generate", _after_generate, {"finalize_declined": "finalize_declined", "validate": "validate"}
    )
    builder.add_edge("finalize_declined", END)
    builder.add_conditional_edges(
        "validate", _after_validate, {"finalize_blocked": "finalize_blocked", "finalize_auto_respond": "finalize_auto_respond"}
    )
    builder.add_edge("finalize_blocked", END)
    builder.add_edge("finalize_auto_respond", END)

    return builder.compile()


# Built once; the compiled graph is stateless between invokes (all per-ticket
# data lives in the state dict passed to .invoke()), so it's safe to reuse
# across every run_ticket() call, sequential or not.
_GRAPH = _build_graph()


def run_ticket(
    raw: dict,
    *,
    client: ChatClient,
    retriever: Retriever,
    log: DecisionLog,
) -> PipelineResult:
    started = time.monotonic()
    final_state: PipelineState = _GRAPH.invoke(
        {"raw": raw, "client": client, "retriever": retriever, "log": log, "started": started}
    )

    return PipelineResult(
        ticket_id=final_state["ticket_id"],
        channel=final_state["channel"],
        classification=final_state["classification"],
        retrieval=final_state["retrieval"],
        routing=final_state.get("routing") or RoutingDecision(
            action=final_state["final_action"],
            reason=final_state.get("escalation_summary") or "",
            threshold_applied=SETTINGS.confidence_threshold,
            forced=True,
        ),
        generation=final_state.get("generation"),
        validation=final_state.get("validation"),
        final_action=final_state["final_action"],
        final_text_sent=final_state.get("final_text_sent"),
        escalation_summary=final_state.get("escalation_summary"),
        error=final_state.get("error"),
        latency_seconds=time.monotonic() - started,
    )
