from src.route.router import route
from src.schemas import (
    ActionTaken,
    ClassificationResult,
    RetrievalResult,
    RetrievedPassage,
    Urgency,
)

PASSAGE = RetrievedPassage(doc_id="DOC-1", title="t", chunk_text="c", score=0.9)


def _classification(intent="billing_query", confidence=0.9, fallback=False):
    return ClassificationResult(intent=intent, urgency=Urgency.LOW, confidence=confidence,
                                 alternatives=[], used_fallback=fallback)


def test_high_confidence_with_grounding_auto_responds():
    decision = route(_classification(confidence=0.9), RetrievalResult(passages=[PASSAGE]))
    assert decision.action == ActionTaken.AUTO_RESPOND


def test_low_confidence_escalates():
    decision = route(_classification(confidence=0.3), RetrievalResult(passages=[PASSAGE]))
    assert decision.action == ActionTaken.ESCALATE
    assert decision.forced is False


def test_always_escalate_intents_override_high_confidence():
    for intent in ["compliance_request", "security_incident", "feature_request", "unclear_request"]:
        decision = route(_classification(intent=intent, confidence=0.99), RetrievalResult(passages=[PASSAGE]))
        assert decision.action == ActionTaken.ESCALATE
        assert decision.forced is True


def test_no_grounding_escalates_even_at_high_confidence():
    decision = route(_classification(confidence=0.99), RetrievalResult(passages=[]))
    assert decision.action == ActionTaken.ESCALATE
    assert decision.forced is True


def test_fallback_classification_forces_escalate():
    decision = route(_classification(fallback=True), RetrievalResult(passages=[PASSAGE]))
    assert decision.action == ActionTaken.ESCALATE
    assert decision.forced is True


def test_routing_is_deterministic_same_input_same_output():
    c = _classification(confidence=0.85)
    r = RetrievalResult(passages=[PASSAGE])
    first = route(c, r)
    second = route(c, r)
    assert first.action == second.action
    assert first.reason == second.reason


def test_threshold_is_respected_at_the_boundary():
    at_threshold = route(_classification(confidence=0.80), RetrievalResult(passages=[PASSAGE]), threshold=0.80)
    just_below = route(_classification(confidence=0.79), RetrievalResult(passages=[PASSAGE]), threshold=0.80)
    assert at_threshold.action == ActionTaken.AUTO_RESPOND
    assert just_below.action == ActionTaken.ESCALATE
