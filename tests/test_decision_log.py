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


def test_count_distinct_tickets_in_scopes_to_one_run(tmp_decision_log):
    """Regression test for a real bug found on 2026-09-16: the decisions.db
    is never cleared between harness runs (by design -- it's an accumulating
    audit trail), but evaluation/harness.py was reconciling a run's decision
    count against count_distinct_tickets() with no scoping, i.e. against the
    WHOLE table. Two runs on the same db (500 tickets, then a disjoint 80)
    reported "decisions_logged": 580 for BOTH runs, and the second run's
    reconciliation check (580 >= 80) passed trivially even though it says
    nothing about whether the 80 tickets in that run were actually logged.
    count_distinct_tickets_in() must answer "how many of THESE ticket_ids
    have a decision logged", not "how many total distinct tickets exist"."""
    # "Run 1": tickets A and B get logged.
    tmp_decision_log.record(ticket_id="A", stage="classification", input_summary="x",
                             action_taken="classified", reason="r")
    tmp_decision_log.record(ticket_id="B", stage="classification", input_summary="x",
                             action_taken="classified", reason="r")

    # "Run 2": a disjoint ticket set, C and D, that this run's own pipeline
    # never actually logged (simulating the exact failure the reconciliation
    # check exists to catch). The old, unscoped count_distinct_tickets()
    # would still return 2 (>= len(["C", "D"]) == 2 tickets is misleading;
    # scaled up this is exactly how 580 >= 80 passed without meaning anything).
    assert tmp_decision_log.count_distinct_tickets_in(["C", "D"]) == 0
    # The whole-table count is unaffected and still reflects run 1's history --
    # this is correct, accumulating behavior, not itself a bug.
    assert tmp_decision_log.count_distinct_tickets() == 2

    # Now "run 2" actually logs C (but not D, e.g. a partial failure) --
    # the scoped count must reflect exactly that, not run 1's leftover total.
    tmp_decision_log.record(ticket_id="C", stage="classification", input_summary="x",
                             action_taken="classified", reason="r")
    assert tmp_decision_log.count_distinct_tickets_in(["C", "D"]) == 1
    assert tmp_decision_log.count_distinct_tickets() == 3


def test_guardrail_activation_counts_scopes_to_one_run(tmp_decision_log):
    """Same cross-run leakage bug, for guardrail_activations_by_type /
    private_data_detections -- these must also be scoped to the run being
    reported on, not summed over every run ever written to this db path."""
    tmp_decision_log.record(
        ticket_id="A", stage="validation", input_summary="x", action_taken="blocked", reason="r",
        guardrail_results={"private_data": False},
    )
    # Unscoped call still sees run 1's history -- correct for a whole-log audit.
    assert tmp_decision_log.guardrail_activation_counts()["private_data"] == 1
    # "Run 2" processes ticket B only, and B triggered no guardrail failures.
    # Scoped to run 2's ticket_ids, there should be zero activations -- not
    # run 1's leftover count.
    tmp_decision_log.record(
        ticket_id="B", stage="validation", input_summary="x", action_taken="auto_respond", reason="r",
        guardrail_results={"private_data": True},
    )
    assert tmp_decision_log.guardrail_activation_counts(["B"]) == {}


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
