"""
Run ONE real ticket through the migrated pipeline against live services:
a real Groq call through langchain_groq.ChatGroq, and a real vector search
through langchain_chroma.Chroma + langchain_huggingface.HuggingFaceEmbeddings
(which downloads the embedding model from huggingface.co on first run).

This needs a working GROQ_API_KEY in .env and internet access -- unlike
scripts/demo_scenario.py, nothing here is scripted, so the exact wording
of the answer will vary between runs. What should NOT vary is the intent,
the routing decision, and which doc gets cited -- that's what to check
against the "Expected" block below.

Run from the repo root:
    python scripts/live_scenario.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm_client import get_default_client
from src.persistence.decision_log import DecisionLog
from src.pipeline import run_ticket
from src.retrieve.retriever import Retriever

TICKET_ID = "VAL-0047"  # a real ticket from data/validation_tickets.json, not invented

tickets = json.loads(Path("data/validation_tickets.json").read_text())
ticket = next(t for t in tickets if t["ticket_id"] == TICKET_ID)

print("=== Ticket ===")
print(f"[{ticket['channel']}] {ticket.get('subject') or '(no subject)'}")
print(ticket["body"])
print()

client = get_default_client()
retriever = Retriever()  # uses SETTINGS.chroma_path, same as the harness
if not retriever.is_indexed():
    print("Indexing documentation corpus (first run only)...")
    retriever.index_documentation("data/documentation.json")

with tempfile.TemporaryDirectory() as tmp:
    log = DecisionLog(path=f"{tmp}/decisions.db")
    result = run_ticket(ticket, client=client, retriever=retriever, log=log)

print("=== Actual result ===")
print("intent            :", result.classification.intent)
print("confidence        :", round(result.classification.confidence, 2))
print("final_action      :", result.final_action.value)
print("retrieved doc_ids :", [p.doc_id for p in result.retrieval.passages])
print("citations         :", result.generation.citations if result.generation else None)
print("validation.blocked:", result.validation.blocked if result.validation else None)
print()
print("--- Answer sent to the customer ---")
print(result.final_text_sent or f"(none -- {result.escalation_summary})")
print()

print("=== Expected (from this ticket's dataset label, for you to compare against) ===")
print("intent            : rate_limit")
print("final_action      : auto_respond")
print("expected doc_ids  : ['DOC-API-001']")
print(
    "answer should say : search/export endpoints have a tighter limit than general "
    "read endpoints, read the rate-limit response headers rather than guessing, and "
    "back off with jitter on 429s instead of retrying immediately"
)
