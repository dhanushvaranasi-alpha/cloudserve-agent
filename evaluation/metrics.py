"""
Every figure in the harness's metrics report is calculated here, by code,
not by hand afterwards (Build Spec A10). Business/technical/governance
groupings match Evaluation_Framework.docx exactly.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any


def volume_metrics(results: list[dict]) -> dict:
    total = len(results)
    auto = sum(1 for r in results if r["final_action"] == "auto_respond")
    escalated = sum(1 for r in results if r["final_action"] == "escalate")
    blocked = sum(1 for r in results if r.get("validation") and r["validation"].get("blocked"))
    errors = sum(1 for r in results if r.get("error"))
    return {
        "tickets_processed": total,
        "answered_automatically": auto,
        "escalated": escalated,
        "blocked_by_guardrail": blocked,
        "ingest_errors": errors,
    }


def business_metrics(results: list[dict]) -> dict:
    total = len(results) or 1
    auto = sum(1 for r in results if r["final_action"] == "auto_respond")
    escalated = sum(1 for r in results if r["final_action"] == "escalate")
    latencies = [r["latency_seconds"] for r in results if r.get("latency_seconds") is not None]

    return {
        "first_contact_resolution_pct": round(100.0 * auto / total, 2),
        "escalation_rate_pct": round(100.0 * escalated / total, 2),
        "mean_response_time_seconds": round(statistics.mean(latencies), 3) if latencies else None,
        "median_response_time_seconds": round(statistics.median(latencies), 3) if latencies else None,
    }


def technical_metrics(results: list[dict], ground_truth: dict[str, dict]) -> dict:
    latencies = sorted(r["latency_seconds"] for r in results if r.get("latency_seconds") is not None)
    p95 = latencies[int(0.95 * len(latencies)) - 1] if latencies else None

    y_true, y_pred = [], []
    retrieval_hits, retrieval_checkable = 0, 0

    for r in results:
        truth = ground_truth.get(r["ticket_id"])
        if truth and "intent" in truth:
            y_true.append(truth["intent"])
            y_pred.append(r["classification"]["intent"])

        expected_docs = set((truth or {}).get("expected_doc_ids") or [])
        if expected_docs:
            retrieval_checkable += 1
            retrieved = {p["doc_id"] for p in r.get("retrieval", {}).get("passages", [])}
            if expected_docs & retrieved:
                retrieval_hits += 1

    precision_recall: dict[str, Any] = {}
    if y_true:
        try:
            from sklearn.metrics import precision_recall_fscore_support

            labels = sorted(set(y_true) | set(y_pred))
            precision, recall, f1, support = precision_recall_fscore_support(
                y_true, y_pred, labels=labels, zero_division=0
            )
            precision_recall = {
                "per_class": {
                    label: {"precision": round(float(p), 3), "recall": round(float(rc), 3), "support": int(s)}
                    for label, p, rc, s in zip(labels, precision, recall, support)
                },
                "overall_accuracy": round(sum(1 for a, b in zip(y_true, y_pred) if a == b) / len(y_true), 3),
            }
        except ImportError:
            precision_recall = {"error": "scikit-learn not available"}

    return {
        "classification": precision_recall,
        "retrieval_hit_rate_pct": round(100.0 * retrieval_hits / retrieval_checkable, 2) if retrieval_checkable else None,
        "retrieval_checkable_tickets": retrieval_checkable,
        "latency_median_seconds": statistics.median(latencies) if latencies else None,
        "latency_p95_seconds": p95,
    }


def governance_metrics(results: list[dict], decisions_logged: int, guardrail_activations: dict[str, int]) -> dict:
    return {
        "decisions_logged": decisions_logged,
        "tickets_processed": len(results),
        "decision_log_reconciles": decisions_logged >= len(results),
        "guardrail_activations_by_type": guardrail_activations,
        "private_data_detections": guardrail_activations.get("private_data", 0),
    }


def calibration_table(results: list[dict], ground_truth: dict[str, dict], bands: int = 5) -> list[dict]:
    """Bin predictions by stated confidence, compare to observed accuracy per band."""
    scored = []
    for r in results:
        truth = ground_truth.get(r["ticket_id"])
        if not truth or "intent" not in truth:
            continue
        conf = r["classification"]["confidence"]
        correct = r["classification"]["intent"] == truth["intent"]
        scored.append((conf, correct))

    rows = []
    for i in range(bands):
        low, high = i / bands, (i + 1) / bands
        group = [c for c in scored if low <= c[0] < high] if i < bands - 1 else [c for c in scored if low <= c[0] <= high]
        if not group:
            continue
        stated = sum(c[0] for c in group) / len(group)
        observed = sum(1 for c in group if c[1]) / len(group)
        rows.append({
            "band": f"{low:.1f}-{high:.1f}", "n": len(group),
            "stated_confidence": round(stated, 3), "observed_accuracy": round(observed, 3),
            "gap": round(stated - observed, 3),
        })
    return rows


def fairness_segments(results: list[dict], tickets_by_id: dict[str, dict]) -> dict:
    """Resolution quality by customer_tier and language_fluency -- the segments
    Dataset_Guide.docx names explicitly for the fairness audit."""
    segments: dict[str, dict[str, list[bool]]] = {"customer_tier": defaultdict(list), "language_fluency": defaultdict(list)}

    for r in results:
        raw = tickets_by_id.get(r["ticket_id"], {})
        resolved = r["final_action"] == "auto_respond"
        tier = raw.get("customer_tier", "unknown")
        fluency = raw.get("language_fluency", "unknown")
        segments["customer_tier"][tier].append(resolved)
        segments["language_fluency"][fluency].append(resolved)

    out: dict[str, dict] = {}
    for dimension, groups in segments.items():
        out[dimension] = {
            key: {"n": len(vals), "resolution_rate_pct": round(100.0 * sum(vals) / len(vals), 2)}
            for key, vals in groups.items() if vals
        }
    return out
