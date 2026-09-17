"""
Decision log: every automated decision, reconstructable months later.

Schema matches Governance_Framework.docx section 1 exactly (the "minimum
record"). SQLite, per the pack's stack notes ("fine for the decision log at
this scale"). One row per pipeline stage-decision, not one row per ticket,
so classification/routing/validation are each independently auditable.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from src.config import SETTINGS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    ticket_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    input_summary TEXT,
    model_name TEXT,
    model_version TEXT,
    prediction_value TEXT,
    prediction_confidence REAL,
    alternatives TEXT,
    sources_used TEXT,
    threshold_applied REAL,
    action_taken TEXT,
    reason TEXT,
    guardrail_results TEXT,
    prompt_version TEXT,
    requirement_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ticket ON decisions(ticket_id);
"""


class DecisionLog:
    def __init__(self, path: str | None = None):
        self._path = path or SETTINGS.decision_log_path
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def record(
        self,
        *,
        ticket_id: str,
        stage: str,
        input_summary: str,
        action_taken: str,
        reason: str,
        model_name: str = "",
        model_version: str = "",
        prediction_value: Optional[str] = None,
        prediction_confidence: Optional[float] = None,
        alternatives: Optional[list] = None,
        sources_used: Optional[list] = None,
        threshold_applied: Optional[float] = None,
        guardrail_results: Optional[dict] = None,
        prompt_version: str = "",
        requirement_ids: Optional[list[str]] = None,
    ) -> str:
        decision_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO decisions
                (decision_id, timestamp, ticket_id, stage, input_summary, model_name,
                 model_version, prediction_value, prediction_confidence, alternatives,
                 sources_used, threshold_applied, action_taken, reason,
                 guardrail_results, prompt_version, requirement_ids)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision_id,
                    datetime.now(timezone.utc).isoformat(),
                    ticket_id,
                    stage,
                    input_summary[:500],
                    model_name,
                    model_version,
                    prediction_value,
                    prediction_confidence,
                    json.dumps(alternatives or []),
                    json.dumps(sources_used or []),
                    threshold_applied,
                    action_taken,
                    reason,
                    json.dumps(guardrail_results or {}),
                    prompt_version,
                    json.dumps(requirement_ids or []),
                ),
            )
        return decision_id

    def count_distinct_tickets(self) -> int:
        """Distinct tickets in the WHOLE log, across every run ever written to
        this db path. The log is an accumulating audit trail by design (see
        module docstring) and is never cleared between harness runs, so this
        number grows across runs -- it answers "how many tickets has this
        system ever logged a decision for", not "did the run I just did log
        correctly". Kept for that broader audit use case; use
        count_distinct_tickets_in() to check a specific run.
        """
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(DISTINCT ticket_id) FROM decisions").fetchone()
            return row[0] if row else 0

    def count_distinct_tickets_in(self, ticket_ids: list[str]) -> int:
        """Distinct tickets FROM ticket_ids that have at least one decision
        logged. Scoped reconciliation check for one run: because the log is
        never cleared between runs (see count_distinct_tickets docstring),
        comparing a run's ticket count against the whole table's distinct
        count silently passes even if that run's own logging failed, as long
        as older runs left enough history in the table. This is the check
        evaluation/harness.py should use for governance.decision_log_reconciles.
        """
        ticket_ids = list(dict.fromkeys(tid for tid in ticket_ids if tid))
        if not ticket_ids:
            return 0
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in ticket_ids)
            row = conn.execute(
                f"SELECT COUNT(DISTINCT ticket_id) FROM decisions WHERE ticket_id IN ({placeholders})",
                ticket_ids,
            ).fetchone()
            return row[0] if row else 0

    def all_for_ticket(self, ticket_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM decisions WHERE ticket_id = ? ORDER BY timestamp", (ticket_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def guardrail_activation_counts(self, ticket_ids: Optional[list[str]] = None) -> dict[str, int]:
        """Guardrail activation counts. Same cross-run scoping issue as
        count_distinct_tickets(): the log is never cleared between runs, so
        with no ticket_ids filter this sums activations over every run ever
        written to this db path, not just the run being reported on. Pass
        this run's ticket_ids (as evaluation/harness.py does) to scope it to
        one run's governance report; omit it only for a whole-log audit.
        """
        counts: dict[str, int] = {}
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            if ticket_ids:
                ticket_ids = list(dict.fromkeys(tid for tid in ticket_ids if tid))
                placeholders = ",".join("?" for _ in ticket_ids)
                rows = conn.execute(
                    f"SELECT guardrail_results FROM decisions "
                    f"WHERE stage = 'validation' AND ticket_id IN ({placeholders})",
                    ticket_ids,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT guardrail_results FROM decisions WHERE stage = 'validation'"
                ).fetchall()
        for row in rows:
            results = json.loads(row["guardrail_results"] or "{}")
            for name, passed in results.items():
                if not passed:
                    counts[name] = counts.get(name, 0) + 1
        return counts
