"""
End-to-end pipeline tests using FakeChatClient + FakeRetriever -- no
network, no API key, no downloaded embedding model. This is what proves
the wiring (ingest -> classify -> retrieve -> route -> generate -> validate
-> decision log) is correct independently of whether Groq is reachable.
"""
import json

from src.llm_client import FakeChatClient, ProviderError
from src.pipeline import run_ticket
from src.retrieve.retriever import FakeRetriever
from src.schemas import ActionTaken, RetrievedPassage

PASSAGE = RetrievedPassage(
    doc_id="DOC-AUTH-001", title="Resolving invalid credential errors", score=0.9,
    chunk_text="1. Reset your password. 2. Clear cached tokens. 3. Retry the login.",
)


def _classify_response(intent="authentication_failure", confidence=0.9):
    return json.dumps({"intent": intent, "urgency": "high", "confidence": confidence, "alternatives": []})


def _generate_response(citations=("DOC-AUTH-001",), declined=False):
    return json.dumps({
        "answer": None if declined else "Please reset your password and clear cached tokens.",
        "citations": [] if declined else list(citations),
        "declined": declined,
        "declined_reason": "not enough information" if declined else None,
    })


def test_high_confidence_grounded_ticket_auto_responds(sample_ticket_email, tmp_decision_log):
    client = FakeChatClient(default=_classify_response())
    client.responses = {"<retrieved_passages>": _generate_response(), "Subject:": _classify_response()}
    retriever = FakeRetriever(fixed_passages=[PASSAGE])

    result = run_ticket(sample_ticket_email, client=client, retriever=retriever, log=tmp_decision_log)

    assert result.final_action == ActionTaken.AUTO_RESPOND
    assert result.final_text_sent is not None
    assert result.error is None
    # every stage should have written to the decision log
    log_rows = tmp_decision_log.all_for_ticket(sample_ticket_email["ticket_id"])
    stages = {row["stage"] for row in log_rows}
    assert {"classification", "retrieval", "routing", "generation", "validation"} <= stages


def test_low_confidence_escalates_without_calling_generate(sample_ticket_email, tmp_decision_log):
    client = FakeChatClient(default=_classify_response(confidence=0.2))
    retriever = FakeRetriever(fixed_passages=[PASSAGE])

    result = run_ticket(sample_ticket_email, client=client, retriever=retriever, log=tmp_decision_log)

    assert result.final_action == ActionTaken.ESCALATE
    assert result.generation is None  # never reached generate at all
    assert result.escalation_summary is not None


def test_always_escalate_intent_skips_generation(sample_ticket_email, tmp_decision_log):
    client = FakeChatClient(default=_classify_response(intent="security_incident", confidence=0.99))
    retriever = FakeRetriever(fixed_passages=[PASSAGE])

    result = run_ticket(sample_ticket_email, client=client, retriever=retriever, log=tmp_decision_log)

    assert result.final_action == ActionTaken.ESCALATE
    assert result.routing.forced is True
    assert result.generation is None


def test_guardrail_block_downgrades_auto_respond_to_escalate(sample_ticket_email, tmp_decision_log):
    client = FakeChatClient(default=_classify_response())
    client.responses = {
        "Subject:": _classify_response(),
        "<retrieved_passages>": json.dumps({
            "answer": "Email me at agent@internal-example.com for help.",
            "citations": ["DOC-AUTH-001"], "declined": False, "declined_reason": None,
        }),
    }
    retriever = FakeRetriever(fixed_passages=[PASSAGE])

    result = run_ticket(sample_ticket_email, client=client, retriever=retriever, log=tmp_decision_log)

    assert result.final_action == ActionTaken.ESCALATE  # private_data guardrail fired
    assert result.final_text_sent is None
    assert result.validation is not None and result.validation.blocked is True


def test_provider_outage_degrades_gracefully_instead_of_crashing(sample_ticket_email, tmp_decision_log):
    class AlwaysDownClient:
        def chat(self, **kwargs):
            raise ProviderError("simulated total outage")

    retriever = FakeRetriever(fixed_passages=[PASSAGE])
    # Must not raise -- A11.
    result = run_ticket(sample_ticket_email, client=AlwaysDownClient(), retriever=retriever, log=tmp_decision_log)
    assert result.final_action == ActionTaken.ESCALATE
    assert result.classification.used_fallback is True


def test_malformed_ticket_does_not_crash_the_pipeline(tmp_decision_log):
    client = FakeChatClient(default=_classify_response())
    retriever = FakeRetriever(fixed_passages=[PASSAGE])
    malformed = {"ticket_id": "MALFORMED-1"}  # no channel, no body

    result = run_ticket(malformed, client=client, retriever=retriever, log=tmp_decision_log)
    assert result.ticket_id == "MALFORMED-1"
    assert result.final_action in (ActionTaken.ESCALATE, ActionTaken.AUTO_RESPOND)


def test_repeated_run_on_identical_ticket_yields_identical_routing(sample_ticket_email, tmp_decision_log):
    client = FakeChatClient(default=_classify_response(confidence=0.5))
    retriever = FakeRetriever(fixed_passages=[PASSAGE])

    first = run_ticket(sample_ticket_email, client=client, retriever=retriever, log=tmp_decision_log)
    second = run_ticket(sample_ticket_email, client=client, retriever=retriever, log=tmp_decision_log)
    assert first.routing.action == second.routing.action
