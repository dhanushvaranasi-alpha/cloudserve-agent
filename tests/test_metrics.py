from evaluation.metrics import (
    business_metrics,
    calibration_table,
    fairness_segments,
    governance_metrics,
    technical_metrics,
    volume_metrics,
)


def _result(ticket_id, action, latency=1.0, intent="billing_query", confidence=0.9, blocked=False, passages=None):
    return {
        "ticket_id": ticket_id,
        "final_action": action,
        "latency_seconds": latency,
        "classification": {"intent": intent, "confidence": confidence},
        "retrieval": {"passages": passages or []},
        "validation": {"blocked": blocked} if action == "escalate" and blocked else {"blocked": False},
        "error": None,
    }


def test_volume_metrics_counts_each_bucket():
    results = [
        _result("1", "auto_respond"),
        _result("2", "escalate"),
        _result("3", "escalate", blocked=True),
    ]
    v = volume_metrics(results)
    assert v["tickets_processed"] == 3
    assert v["answered_automatically"] == 1
    assert v["escalated"] == 2
    assert v["blocked_by_guardrail"] == 1


def test_business_metrics_fcr_and_escalation_rate():
    results = [_result(str(i), "auto_respond") for i in range(6)] + [_result(str(i), "escalate") for i in range(4)]
    b = business_metrics(results)
    assert b["first_contact_resolution_pct"] == 60.0
    assert b["escalation_rate_pct"] == 40.0


def test_technical_metrics_classification_accuracy():
    results = [
        _result("1", "auto_respond", intent="billing_query"),
        _result("2", "escalate", intent="rate_limit"),
    ]
    ground_truth = {"1": {"intent": "billing_query"}, "2": {"intent": "rate_limit"}}
    t = technical_metrics(results, ground_truth)
    assert t["classification"]["overall_accuracy"] == 1.0


def test_technical_metrics_retrieval_hit_rate():
    results = [_result("1", "auto_respond", passages=[{"doc_id": "DOC-1", "score": 0.9}])]
    ground_truth = {"1": {"intent": "billing_query", "expected_doc_ids": ["DOC-1"]}}
    t = technical_metrics(results, ground_truth)
    assert t["retrieval_hit_rate_pct"] == 100.0


def test_governance_metrics_reconciliation():
    results = [_result("1", "auto_respond"), _result("2", "escalate")]
    g = governance_metrics(results, decisions_logged=10, guardrail_activations={"private_data": 1})
    assert g["decision_log_reconciles"] is True
    assert g["private_data_detections"] == 1


def test_calibration_table_groups_by_confidence_band():
    results = [_result(str(i), "auto_respond", intent="billing_query", confidence=0.85) for i in range(4)]
    ground_truth = {str(i): {"intent": "billing_query"} for i in range(4)}
    table = calibration_table(results, ground_truth, bands=5)
    band = next(r for r in table if r["band"] == "0.8-1.0")
    assert band["observed_accuracy"] == 1.0
    assert band["n"] == 4


def test_fairness_segments_by_tier():
    results = [_result("1", "auto_respond"), _result("2", "escalate")]
    tickets_by_id = {
        "1": {"customer_tier": "enterprise", "language_fluency": "fluent"},
        "2": {"customer_tier": "standard", "language_fluency": "non_fluent"},
    }
    segments = fairness_segments(results, tickets_by_id)
    assert segments["customer_tier"]["enterprise"]["resolution_rate_pct"] == 100.0
    assert segments["customer_tier"]["standard"]["resolution_rate_pct"] == 0.0
