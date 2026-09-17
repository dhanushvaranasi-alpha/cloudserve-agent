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

How to read this file if you're new to LangGraph:
  1. `PipelineState` is the single dict-like object that flows through the
     whole run. Every node reads from it and returns a partial dict of
     updates; LangGraph merges that partial dict back into the state before
     handing it to the next node. Nothing is passed as function arguments
     between nodes -- state is the only channel.
  2. Each `_..._node` function is one pipeline stage. A node's job is:
     read what it needs from state, call the real component (classify,
     retrieve, route, generate, run_guardrails), write a decision-log row,
     and return the piece of state it produced.
  3. Each `_after_...` function is a conditional-edge router: it looks at
     the state *after* a node ran and returns the string name of whichever
     node should run next. This is what encodes the pipeline's branching
     logic (early-exit to escalate, decline, or guardrail-block) instead of
     if/else statements.
  4. `_finalize_..._node` functions don't call any pipeline component -- they
     just assemble the terminal `final_action` / `final_text_sent` /
     `escalation_summary` fields for one of the ways a run can end, then the
     graph routes straight to END.
  5. `_build_graph()` registers every node and edge once. `_GRAPH` is the
     compiled result, built a single time when this module is imported.
  6. `run_ticket()` is the public entry point everything else calls. It
     just seeds the initial state and calls `_GRAPH.invoke(...)`, then
     reshapes the final state into the `PipelineResult` Pydantic model the
     rest of the codebase (API, harness, tests) already expects.
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
    """
    The one object every node reads from and writes to.

    `total=False` means no key is required to be present at every point in
    the run -- that's intentional, since different branches populate
    different subsets of these fields (e.g. an ingest failure never sets
    `classification` from the real classifier, and an escalated ticket
    never sets `generation` or `validation` at all). Each node's return
    value is a partial dict; LangGraph shallow-merges it into this state
    before the next node runs, so a node only needs to return the keys it
    actually changed.
    """
    # Inputs / dependencies, set once before graph.invoke() and read-only
    # from every node's point of view. These exist so the per-ticket chat
    # client, retriever and decision log can reach every node without
    # relying on closures, since a compiled graph's nodes are plain
    # functions of state, not methods bound to a particular run.
    raw: dict
    client: ChatClient
    retriever: Retriever
    log: DecisionLog
    started: float

    # Populated by ingest_node: the normalized ticket and its id/channel,
    # plus a flag marking whether normalization itself failed.
    ticket_id: str
    channel: Channel
    ticket: NormalizedTicket
    ingest_failed: bool

    # Populated by later nodes, one field per pipeline stage. These stay
    # unset (missing from the dict) on branches that skip that stage --
    # for example `generation` and `validation` are never set on a ticket
    # that gets routed straight to escalate.
    classification: ClassificationResult
    retrieval: RetrievalResult
    routing: RoutingDecision
    generation: Optional[GenerationResult]
    validation: Optional[ValidationResult]

    # Final result fields. Every run, whichever path it takes through the
    # graph, ends with all four of these set by one of the
    # `_finalize_..._node` functions just before hitting END.
    final_action: ActionTaken
    final_text_sent: Optional[str]
    escalation_summary: Optional[str]
    error: Optional[str]


def _ingest_node(state: PipelineState) -> dict:
    """
    Graph entry point. Normalizes the raw ticket dict into a
    `NormalizedTicket` (channel-specific parsing, whitespace cleanup, id
    coercion, etc. -- see src/ingest/ingest.py for the actual logic).

    This node is deliberately defensive: FR-11 requires that ingest can
    never crash an unattended batch run. If `normalize_ticket` itself
    raises (malformed input the fallback parsing couldn't handle), this
    node catches it, logs a synthetic "ingest_error" decision row, and
    returns a fully-formed escalate result -- so the rest of the graph
    (classify/retrieve/route/generate/validate) is skipped entirely via
    the `ingest_failed` flag and the conditional edge right after this
    node, rather than the exception propagating up and killing the batch.
    """
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
        # Build placeholder classification/retrieval/routing objects so the
        # eventual PipelineResult is still a valid, fully-typed object even
        # though none of the real classify/retrieve/route logic ever ran.
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
        # ingest_failed=True is what the conditional edge below checks to
        # route straight to END, skipping every other node in the graph.
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

    # Happy path: ingest succeeded, so just record the normalized ticket
    # and let the graph continue on to classify.
    return {
        "ingest_failed": False,
        "ticket": ticket,
        "ticket_id": ticket.ticket_id,
        "channel": ticket.channel,
    }


def _classify_node(state: PipelineState) -> dict:
    """
    Calls the real classifier (an LLM call through `client`, see
    src/classify/classifier.py) to predict intent, urgency and confidence
    for the ticket, then writes a "classification" decision-log row and
    returns the result so downstream nodes (retrieve, route, generate) can
    read it back out of state.
    """
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
    """
    Runs the vector-store similarity search (Chroma via
    src/retrieve/retriever.py) against the ticket text to find grounding
    passages from the documentation corpus, regardless of what the
    classifier predicted -- retrieval always runs so the router has
    grounding information available no matter which intent came back.
    Logs which doc_ids/scores were retrieved (or that none cleared the
    relevance threshold) and returns the retrieval result.
    """
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
    """
    Pure decision logic (no LLM call): looks at the classification
    confidence and whether retrieval found any grounding passages, and
    decides whether this ticket is even a candidate for an automated
    response (`AUTO_RESPOND`) or should be escalated straight away
    (`ESCALATE`) -- e.g. low confidence, or no supporting documentation
    found. See src/route/router.py for the actual thresholding rule.
    """
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
    """
    Conditional edge after "route". This is the first of the pipeline's
    three early-exit branch points: if routing decided AUTO_RESPOND, go on
    to generate() an answer; for anything else (ESCALATE, forced-escalate
    from low confidence or no grounding), skip generate() and validate()
    entirely and go straight to the escalate finalizer.
    """
    return "generate" if state["routing"].action == ActionTaken.AUTO_RESPOND else "finalize_routed_escalate"


def _finalize_routed_escalate_node(state: PipelineState) -> dict:
    """
    Terminal node for the "routed straight to escalate" branch (no
    generate/validate ever ran). Builds a human-readable summary an agent
    would see in a queue -- why it was escalated, what the classifier
    predicted, and which sources (if any) were retrieved -- and sets the
    final result fields. The graph edges straight from here to END.
    """
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
    """
    Only reached when routing chose AUTO_RESPOND. Calls the generator (an
    LLM call through `client`, see src/generate/generator.py) to draft a
    grounded answer from the retrieved passages. The generator itself can
    still choose to decline (e.g. the retrieved passages don't actually
    answer the question) -- that's `generation.declined`, checked by the
    next conditional edge, not by this node. Logs whether it drafted or
    declined and which doc_ids were cited.
    """
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
    """
    Conditional edge after "generate". Second early-exit branch point: if
    the generator declined to answer, skip validate() (there's no draft to
    validate) and go straight to the decline finalizer; otherwise send the
    draft on to the guardrail checks in validate().
    """
    return "finalize_declined" if state["generation"].declined else "validate"


def _finalize_declined_node(state: PipelineState) -> dict:
    """
    Terminal node for "generator declined to answer". Same shape as the
    routed-escalate finalizer above, but the summary explains *why*
    generation itself refused (e.g. it judged the retrieved passages
    insufficient) rather than why routing skipped generation.
    """
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
    """
    Only reached when generate() produced a real draft. Runs the
    guardrail checks (see src/validate/guardrails.py -- things like PII
    leakage, tone, unsupported claims) against the drafted answer. Logs
    each guardrail's pass/fail individually plus the overall blocked
    verdict, and returns the validation result for the final conditional
    edge to inspect.
    """
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
    """
    Conditional edge after "validate" -- the third and last branch point.
    If any guardrail failed, the draft is withheld from the customer and
    the ticket is downgraded to escalate even though generation succeeded;
    otherwise the draft is cleared to actually send.
    """
    return "finalize_blocked" if state["validation"].blocked else "finalize_auto_respond"


def _finalize_blocked_node(state: PipelineState) -> dict:
    """
    Terminal node for "a real draft existed but a guardrail blocked it".
    Note `final_text_sent` is explicitly None here -- the drafted answer
    is deliberately withheld from the customer, even though it was
    generated, because it failed at least one safety/quality check.
    """
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
    """
    Terminal node for the one success path all the way through: routed to
    auto-respond, generation produced an answer, and every guardrail
    passed. This is the only finalizer that actually sets
    `final_text_sent` to real customer-facing text.
    """
    return {
        "final_action": ActionTaken.AUTO_RESPOND,
        "final_text_sent": state["generation"].answer_text,
        "escalation_summary": None,
    }


def _build_graph():
    """
    Registers every node and every edge exactly once, then compiles the
    graph into an executable object. This mirrors, node for node and
    branch for branch, the if/else control flow the pre-migration version
    of this file had:

        ingest -> [ingest_failed?] -> END
                          |
                          v (ok)
                       classify -> retrieve -> route -> [AUTO_RESPOND?]
                                                              |      \\
                                                              v       v
                                                          generate   finalize_routed_escalate -> END
                                                              |
                                                     [declined?]
                                                        |      \\
                                                        v       v
                                                    validate   finalize_declined -> END
                                                        |
                                               [blocked?]
                                                  |      \\
                                                  v       v
                              finalize_blocked -> END    finalize_auto_respond -> END

    `add_edge` wires an unconditional hop (always go from A to B).
    `add_conditional_edges` wires a hop whose destination is decided at
    run time by calling the given router function (`_after_route` etc.)
    with the current state; the dict argument maps each string the router
    can return to the actual node name (or END) to go to.
    """
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

    # Every run starts at ingest.
    builder.add_edge(START, "ingest")
    # Branch point 1: ingest failure short-circuits straight to END with
    # the synthetic escalate result already assembled by _ingest_node;
    # otherwise proceed to classify.
    builder.add_conditional_edges(
        "ingest", lambda s: END if s["ingest_failed"] else "classify", {"classify": "classify", END: END}
    )
    # classify -> retrieve -> route always run in sequence, unconditionally,
    # once ingest has succeeded.
    builder.add_edge("classify", "retrieve")
    builder.add_edge("retrieve", "route")
    # Branch point 2 (_after_route): AUTO_RESPOND continues to generate;
    # anything else finalizes as a routed escalate and ends the run.
    builder.add_conditional_edges(
        "route", _after_route, {"generate": "generate", "finalize_routed_escalate": "finalize_routed_escalate"}
    )
    builder.add_edge("finalize_routed_escalate", END)
    # Branch point 3 (_after_generate): a decline finalizes and ends the
    # run; a real draft continues on to validate.
    builder.add_conditional_edges(
        "generate", _after_generate, {"finalize_declined": "finalize_declined", "validate": "validate"}
    )
    builder.add_edge("finalize_declined", END)
    # Branch point 4 (_after_validate): a guardrail block finalizes as
    # escalate with the draft withheld; a clean pass finalizes as the
    # actual auto-respond success path. Both are terminal.
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
    """
    Public entry point: run one raw ticket dict through the full graph and
    return a fully-populated `PipelineResult`. This signature and return
    type are unchanged from the pre-migration version, so every caller
    (the FastAPI app, the evaluation harness, the test suite) needed zero
    changes for this migration.

    Seeds the initial state with the raw ticket plus the three per-run
    dependencies (client, retriever, log) that every node needs but none
    of them own, then lets `_GRAPH.invoke(...)` walk the graph from START
    to whichever END it reaches. `.invoke()` runs synchronously and
    returns the final merged state once the run hits END.
    """
    started = time.monotonic()
    final_state: PipelineState = _GRAPH.invoke(
        {"raw": raw, "client": client, "retriever": retriever, "log": log, "started": started}
    )

    # Reshape the final graph state into the PipelineResult model the rest
    # of the codebase expects. Most fields are a direct copy out of state;
    # `routing` has one wrinkle: on the ingest-failure branch, _ingest_node
    # already puts a real RoutingDecision object into state, but if some
    # future branch ever left it unset, this falls back to synthesizing one
    # from final_action/escalation_summary so PipelineResult never sees a
    # missing routing field.
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
