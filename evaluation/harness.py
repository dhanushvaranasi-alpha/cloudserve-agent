"""
The evaluation harness -- acceptance criteria A9 and A10.

Run with:
    python -m evaluation.harness --input data/validation_tickets.json --output evaluation/results/

Takes an input path and an output path as arguments (never a hardcoded
filename), because after submission this is run against a hidden file
that has never been seen (Build Spec, section 4). Processes every ticket
in the input file, unattended, and writes a metrics report without further
manual work.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.config import SETTINGS
from src.llm_client import get_default_client
from src.persistence.decision_log import DecisionLog
from src.pipeline import run_ticket
from src.retrieve.retriever import Retriever

from evaluation.metrics import (
    business_metrics,
    calibration_table,
    fairness_segments,
    governance_metrics,
    technical_metrics,
    volume_metrics,
)

logger = logging.getLogger(__name__)


def _load_json(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and "tickets" in data:
        data = data["tickets"]
    if not isinstance(data, list):
        raise ValueError(f"{path} does not contain a list of tickets")
    return data


def run_evaluation(
    input_path: str,
    output_path: str,
    *,
    docs_path: str = "data/documentation.json",
    decision_log_path: str | None = None,
    chroma_path: str | None = None,
) -> dict:
    started_at = datetime.now(timezone.utc)
    out_dir = Path(output_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    tickets = _load_json(input_path)
    logger.info("Loaded %d tickets from %s", len(tickets), input_path)

    client = get_default_client()
    retriever = Retriever(chroma_path=chroma_path)
    if not retriever.is_indexed():
        logger.info("Indexing documentation corpus from %s ...", docs_path)
        retriever.index_documentation(docs_path)

    log = DecisionLog(path=decision_log_path)

    results: list[dict] = []
    run_started = time.monotonic()
    for i, raw in enumerate(tickets, start=1):
        # No manual intervention, no restarts, no skipping: every ticket in
        # the input produces a result, whatever goes wrong inside it.
        try:
            result = run_ticket(raw, client=client, retriever=retriever, log=log)
            results.append(result.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 -- the harness itself must never stop
            logger.exception("Unhandled error processing ticket %s", raw.get("ticket_id"))
            results.append({
                "ticket_id": raw.get("ticket_id", f"UNKNOWN-{i}"),
                "channel": raw.get("channel", "email"),
                "classification": {"intent": "unclear_request", "urgency": "medium",
                                    "confidence": 0.0, "alternatives": [], "used_fallback": True},
                "retrieval": {"passages": [], "query": ""},
                "routing": {"action": "escalate", "reason": f"unhandled error: {exc}",
                            "threshold_applied": SETTINGS.confidence_threshold, "forced": True},
                "generation": None, "validation": None,
                "final_action": "escalate", "final_text_sent": None,
                "escalation_summary": f"Unhandled pipeline error: {exc}",
                "error": str(exc), "latency_seconds": 0.0,
            })
        if i % 20 == 0 or i == len(tickets):
            logger.info("Processed %d/%d tickets", i, len(tickets))

    total_wall_seconds = time.monotonic() - run_started

    ground_truth = {}
    tickets_by_id = {t.get("ticket_id"): t for t in tickets}
    for t in tickets:
        labels = t.get("labels")
        if labels:
            ground_truth[t["ticket_id"]] = {
                "intent": labels.get("intent"),
                "expected_doc_ids": labels.get("expected_doc_ids") or [],
            }

    report = {
        "run": {
            "started_at": started_at.isoformat(),
            "input_path": str(input_path),
            "tickets_in_input": len(tickets),
            "total_wall_seconds": round(total_wall_seconds, 2),
            "model_name": SETTINGS.model_name,
            "classifier_model": SETTINGS.classifier_model,
            "confidence_threshold": SETTINGS.confidence_threshold,
        },
        "volume": volume_metrics(results),
        "business": business_metrics(results),
        "technical": technical_metrics(results, ground_truth),
        "governance": governance_metrics(
            results, log.count_distinct_tickets(), log.guardrail_activation_counts()
        ),
        "calibration": calibration_table(results, ground_truth),
        "fairness_segments": fairness_segments(results, tickets_by_id),
    }

    (out_dir / "metrics_report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "per_ticket_results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in results)
    )
    logger.info("Wrote metrics_report.json and per_ticket_results.jsonl to %s", out_dir)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CloudServe support system, unattended, over a ticket file.")
    parser.add_argument("--input", required=True, help="Path to a ticket JSON file (dev/validation/hidden schema).")
    parser.add_argument("--output", required=True, help="Directory to write metrics_report.json and per-ticket results to.")
    parser.add_argument("--docs", default="data/documentation.json", help="Path to the documentation corpus.")
    args = parser.parse_args()

    report = run_evaluation(args.input, args.output, docs_path=args.docs)
    print(json.dumps(report["volume"], indent=2))
    print(json.dumps(report["business"], indent=2))


if __name__ == "__main__":
    main()
