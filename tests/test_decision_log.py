def test_record_and_retrieve_round_trip(tmp_decision_log):
    tmp_decision_log.record(
        ticket_id="T-1", stage="classification", input_summary="hello",
        action_taken="classified", reason="predicted intent=billing_query",
        prediction_value="billing_query", prediction_confidence=0.9,
        requirement_ids=["FR-02"],
    )
    rows = tmp_decision_log.all_for_ticket("T-1")
    assert len(rows) == 1
    assert rows[0]["stage"] == "classification"
    assert rows[0]["prediction_value"] == "billing_query"


def test_count_distinct_tickets(tmp_decision_log):
    tmp_decision_log.record(ticket_id="A", stage="classification", input_summary="x",
                             action_taken="classified", reason="r")
    tmp_decision_log.record(ticket_id="A", stage="routing", input_summary="x",
                             action_taken="escalate", reason="r")
    tmp_decision_log.record(ticket_id="B", stage="classification", input_summary="x",
                             action_taken="classified", reason="r")
    assert tmp_decision_log.count_distinct_tickets() == 2


def test_guardrail_activation_counts(tmp_decision_log):
    tmp_decision_log.record(
        ticket_id="A", stage="validation", input_summary="x", action_taken="blocked", reason="r",
        guardrail_results={"private_data": False, "grounding": True},
    )
    tmp_decision_log.record(
        ticket_id="B", stage="validation", input_summary="x", action_taken="blocked", reason="r",
        guardrail_results={"private_data": False, "tone_and_scope": False},
    )
    counts = tmp_decision_log.guardrail_activation_counts()
    assert counts["private_data"] == 2
    assert counts["tone_and_scope"] == 1
    assert "grounding" not in counts  # only failures are counted as activations
