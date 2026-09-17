"""
FastAPI interface. Not the primary grading path (the harness is -- A9), but
gives a real HTTP surface for the live demo in the video and for manual
poking during the build.

Also serves the demo UI (web/index.html) at "/" -- a single-page chat-style
front end for the video: submit a ticket, watch the pipeline's real decision
(classification, retrieval, routing, generation, guardrails) come back from
the same endpoints below, no separate server or build step needed.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.llm_client import get_default_client
from src.persistence.decision_log import DecisionLog
from src.pipeline import run_ticket
from src.retrieve.retriever import Retriever

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="CloudServe Support System", version="0.1.0")

_WEB_DIR = Path(__file__).resolve().parents[2] / "web"


@app.get("/")
def demo_ui():
    return FileResponse(_WEB_DIR / "index.html")

_client = None
_retriever = None
_log = None


def _get_state():
    global _client, _retriever, _log
    if _client is None:
        _client = get_default_client()
        _retriever = Retriever()
        if not _retriever.is_indexed():
            _retriever.index_documentation("data/documentation.json")
        _log = DecisionLog()
    return _client, _retriever, _log


class TicketIn(BaseModel):
    ticket_id: str
    channel: str
    subject: str = ""
    body: str
    received_at: str | None = None
    customer_id: str | None = None
    customer_tier: str | None = None
    customer_region: str | None = None
    language_fluency: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tickets")
def submit_ticket(ticket: TicketIn):
    client, retriever, log = _get_state()
    try:
        result = run_ticket(ticket.model_dump(), client=client, retriever=retriever, log=log)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled error for ticket %s", ticket.ticket_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result.model_dump(mode="json")


@app.get("/decisions/{ticket_id}")
def get_decisions(ticket_id: str):
    _, _, log = _get_state()
    rows = log.all_for_ticket(ticket_id)
    if not rows:
        raise HTTPException(status_code=404, detail="no decisions logged for this ticket")
    return rows
