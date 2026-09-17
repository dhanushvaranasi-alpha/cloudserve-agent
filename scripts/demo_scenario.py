"""
One deterministic ticket through the LangGraph pipeline, no network or API
key needed (FakeChatClient/FakeRetriever stand in for Groq/Chroma, same as
the test suite). Run it, compare the printed output to the expected block
in the PR/README, and you've cross-verified the LangGraph wiring end to end
without depending on a live Groq call's non-deterministic wording.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm_client import FakeChatClient
from src.persistence.decision_log import DecisionLog
from src.pipeline import run_ticket
from src.retrieve.retriever import FakeRetriever
from src.schemas import RetrievedPassage

ticket = {
    "ticket_id": "DEMO-001",
    "channel": "email",
    "subject": "Cannot log in to the console",
    "body": "I keep getting an invalid credentials error when I try to sign in.",
    "received_at": "2026-03-14T09:22:00Z",
    "customer_id": "CUST-1042",
    "customer_tier": "business",
    "customer_region": "europe",
    "language_fluency": "fluent",
}

classify_response = json.dumps({
    "intent": "authentication_failure",
    "urgency": "high",
    "confidence": 0.92,
    "alternatives": [],
})

generate_response = json.dumps({
    "answer": "Please reset your password and clear cached tokens, then retry the login.",
    "citations": ["DOC-AUTH-001"],
    "declined": False,
    "declined_reason": None,
})

client = FakeChatClient(default=classify_response)
client.responses = {
    "Subject:": classify_response,          # classify() prompt contains "Subject:"
    "<retrieved_passages>": generate_response,  # generate() prompt contains this tag
}

passage = RetrievedPassage(
    doc_id="DOC-AUTH-001",
    title="Resolving invalid credential errors",
    chunk_text="1. Reset your password. 2. Clear cached tokens. 3. Retry the login.",
    score=0.9,
)
retriever = FakeRetriever(fixed_passages=[passage])

with tempfile.TemporaryDirectory() as tmp:
    log = DecisionLog(path=f"{tmp}/decisions.db")
    result = run_ticket(ticket, client=client, retriever=retriever, log=log)

    print("final_action     :", result.final_action.value)
    print("intent            :", result.classification.intent)
    print("confidence        :", result.classification.confidence)
    print("routing.forced    :", result.routing.forced)
    print("final_text_sent   :", result.final_text_sent)
    print("citations         :", result.generation.citations)
    print("validation.blocked:", result.validation.blocked)
    print("error             :", result.error)

    rows = log.all_for_ticket("DEMO-001")
    print("decision_log stages:", [r["stage"] for r in rows])
