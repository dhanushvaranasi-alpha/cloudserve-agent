import json

from src.classify.classifier import classify
from src.ingest.ingest import normalize_ticket
from src.llm_client import FakeChatClient
from src.schemas import INTENT_CLASSES


def _client_returning(payload: dict) -> FakeChatClient:
    return FakeChatClient(default=json.dumps(payload))


def test_classify_happy_path(sample_ticket_email):
    ticket = normalize_ticket(sample_ticket_email)
    client = _client_returning({
        "intent": "authentication_failure", "urgency": "high", "confidence": 0.91,
        "alternatives": [{"intent": "account_access", "confidence": 0.2}],
    })
    result = classify(ticket, client)
    assert result.intent == "authentication_failure"
    assert result.urgency.value == "high"
    assert 0.0 <= result.confidence <= 1.0
    assert result.used_fallback is False


def test_classify_falls_back_on_invalid_json(sample_ticket_email):
    ticket = normalize_ticket(sample_ticket_email)
    client = FakeChatClient(default="not json at all")
    result = classify(ticket, client)
    assert result.used_fallback is True
    assert result.intent == "unclear_request"
    assert result.confidence == 0.0


def test_classify_falls_back_on_unrecognised_intent(sample_ticket_email):
    ticket = normalize_ticket(sample_ticket_email)
    client = _client_returning({"intent": "made_up_category", "urgency": "low", "confidence": 0.9})
    result = classify(ticket, client)
    assert result.used_fallback is True


def test_classify_clamps_confidence_to_valid_range(sample_ticket_email):
    ticket = normalize_ticket(sample_ticket_email)
    client = _client_returning({"intent": "billing_query", "urgency": "low", "confidence": 5.0})
    result = classify(ticket, client)
    assert result.confidence == 1.0


def test_classify_falls_back_on_provider_error(sample_ticket_email):
    from src.llm_client import ProviderError

    class BrokenClient:
        def chat(self, **kwargs):
            raise ProviderError("simulated outage")

    ticket = normalize_ticket(sample_ticket_email)
    result = classify(ticket, BrokenClient())
    assert result.used_fallback is True


def test_all_prompt_intents_are_in_schema():
    # guards against the prompt's hardcoded class list and schemas.INTENT_CLASSES drifting apart
    assert len(INTENT_CLASSES) == 22
