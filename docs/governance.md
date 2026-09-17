# Governance — CloudServe support automation system

Structure follows `Governance_Framework.docx` exactly. Numbers marked
**[pending run]** are filled in once `evaluation/harness.py` has been run
against `validation_tickets.json` on Dhanush's machine — everything else
here describes mechanisms that are already built and testable today.

## 1. Decision logging

Every automated decision (classification, retrieval, routing, generation,
validation) is written to `storage/decisions.db` (SQLite) by
`src/persistence/decision_log.py`, with this record per decision:

```json
{
  "decision_id": "...", "timestamp": "...", "ticket_id": "...",
  "stage": "classification | routing | generation | validation",
  "input_summary": "...", "model": {"name": "...", "version": "..."},
  "prediction": {"value": "...", "confidence": 0.00},
  "alternatives": [...], "sources_used": [...], "threshold_applied": 0.00,
  "action_taken": "auto_respond | escalate | block",
  "reason": "human-readable explanation",
  "guardrail_results": {...}, "prompt_version": "PR-02 v1.0",
  "requirement_ids": ["FR-05", "FR-06"]
}
```

**Coverage check:** `governance.decision_log_reconciles` is computed
automatically in every `metrics_report.json` by comparing the run's own
ticket set against `DecisionLog.count_distinct_tickets_in(ticket_ids)`,
this is checked by the harness itself on every run, not audited by hand
afterward.

**A real bug this caught (2026-09-16):** the decision log db is never
cleared between runs, by design, it's meant to be an accumulating audit
trail across the system's lifetime (`count_distinct_tickets()`, no
scoping, is kept for that whole-log-audit use case). The harness
originally reconciled *any* run against that whole-table count, so two
runs on the same db (500 tickets, then a disjoint 80) both reported
`"decisions_logged": 580` and `decision_log_reconciles: true`, because
580 ≥ either run's ticket count regardless of whether that run's own
tickets were actually logged. Fixed by scoping the reconciliation (and
`guardrail_activations_by_type` / `private_data_detections`, which had the
same issue) to the current run's `ticket_id`s via
`count_distinct_tickets_in()` / `guardrail_activation_counts(ticket_ids)`.
Regression tests: `tests/test_decision_log.py::test_count_distinct_tickets_in_scopes_to_one_run`
and `::test_guardrail_activation_counts_scopes_to_one_run`.

## 2. Risk register

| ID | Risk | Likelihood | Impact | Mitigation in design | Owner |
|---|---|---|---|---|---|
| R-01 | System answers confidently and incorrectly | Medium | High | Confidence threshold + calibration check (`evaluation/harness.py::calibration_table`); grounding guardrail blocks unsupported citations | Dhanush Varanasi |
| R-02 | Private data appears in an outbound response | Low-Medium | High | `private_data_guardrail` — deterministic regex, blocks and escalates, never redacts-and-sends (per Governance_Framework's own rule) | Dhanush Varanasi |
| R-03 | Customer input treated as an instruction | Low | High | Ticket text isolated in `<ticket>` tags, system prompt explicitly instructs the model to never treat its contents as commands; `instruction_integrity_guardrail` checks for injection markers | Dhanush Varanasi |
| R-04 | Some customer groups receive worse answers | Medium | Medium-High | Fairness audit segments by tier/fluency (§3); retrieval-on-phrasing is a named, tested risk for non-fluent tickets, not an assumed non-issue | Dhanush Varanasi |
| R-05 | Documentation the system relies on goes stale | Low (3-week project horizon) | Medium | `last_reviewed_days_ago` field exists in the corpus schema; out of scope for v1 to auto-refresh (see PRD §6), flagged as a known limitation | Dhanush Varanasi |
| R-06 | Model provider becomes unavailable | Medium (free tier) | Medium | `tenacity` retry/backoff (3 attempts); `UnavailableChatClient` fallback forces safe escalation rather than crashing (A11) | Dhanush Varanasi |
| R-07 | Latency degrades under load | Low (project scale, ~500 tickets) | Low-Medium | p95 latency measured and reported every run (`technical.latency_p95_seconds`); Groq chosen partly for inference speed | Dhanush Varanasi |
| R-08 | Costs rise unexpectedly with volume | Low | Low | Free tier only, no paid capacity used; classifier uses a smaller/cheaper model (`openai/gpt-oss-20b`) than generation | Dhanush Varanasi |
| R-09 | Guardrail regex has false negatives on private data it wasn't written for | Medium | High | Documented limitation: guardrails are pattern-based, not a full PII-detection model; stated explicitly in report rather than implied to be complete | Dhanush Varanasi |

## 3. Fairness audit

Segments per `Dataset_Guide.docx`: `customer_tier`, `customer_region`,
`language_fluency`. `evaluation/metrics.py::fairness_segments` computes
resolution rate by tier and fluency automatically on every harness run —
this is not a manual spreadsheet exercise.

| Segment | Tickets in segment | Resolution rate | Variation from best | Explanation |
|---|---|---|---|---|
| Enterprise customers | **[pending run]** | | | |
| Business customers | **[pending run]** | | | |
| Standard customers | **[pending run]** | | | |
| Fluent English | **[pending run]** | | | |
| Non-fluent English | **[pending run]** | | | |

**What we expect to find, and why it matters going in:** the interviews
(Sofia) and the Governance Framework's own stated failure mode both point
at retrieval-on-phrasing hurting non-fluent tickets specifically — a
retrieval system matches on how the query is worded, and non-fluent
phrasing diverges further from documentation language. The dev-set
historical data was mixed on this (see `docs/PRD.md` §2, point 4), so this
audit is a real test, not confirmation of an assumed conclusion. If the gap
appears, it becomes the headline finding for the PRD revision (Stage 5).

## 4. Guardrails

| Guardrail | What it checks | What happens when it fires | Deterministic? |
|---|---|---|---|
| `private_data` | Email addresses, phone-like digit runs, card-like digit runs, API-key-shaped tokens, "account number" phrases | Block and escalate | Yes (regex, no model call) |
| `grounding` | Every citation resolves to a passage actually retrieved | Block and escalate, unsupported claim flagged in the escalation summary | Yes |
| `instruction_integrity` | Ticket text for injection markers ("ignore the above", "you are now", etc.) | Block, escalate, input recorded for review | Yes |
| `tone_and_scope` | Response for refund/commitment/deadline language outside support scope | Block | Yes |
| `confidence_floor` | Confidence score was actually produced (not a fallback/missing value) | Escalate — a missing score is treated as a low one, not a high one | Yes |

All five run on every generated response, not only in testing —
`src/pipeline.py` calls `run_guardrails()` unconditionally before any
`AUTO_RESPOND` result is finalized, and `tests/test_pipeline.py::test_guardrail_block_downgrades_auto_respond_to_escalate`
proves a guardrail hit downgrades the final action rather than merely
logging a warning (Build Spec A7: "a guardrail that only warns is not a
guardrail").

## 5. Incident response

| Step | What to do | Who does it | How long |
|---|---|---|---|
| 1. Detect | Guardrail activation spike or a manual report of a bad response, visible in `metrics_report.json`'s `governance.guardrail_activations_by_type` or by querying `storage/decisions.db` for a ticket_id | On-call (Dhanush, solo project) | Immediate — logged automatically at time of occurrence |
| 2. Contain | Flip the kill switch (§6) to stop new auto-responses | Dhanush | Under 1 minute (env var + restart) |
| 3. Assess | Query `all_for_ticket(ticket_id)` in the decision log to reconstruct exactly what was predicted, retrieved, and why it was or wasn't blocked | Dhanush | Minutes — the whole point of the decision log schema |
| 4. Notify | For a real deployment: notify affected customer + Marcus (Head of Support); for this project: document in the incident log/report | Dhanush | Same day |
| 5. Remediate | Fix the guardrail/prompt/threshold that let it through; add a regression test reproducing the case in `tests/` | Dhanush | Before re-enabling auto-respond |
| 6. Review | Add the case to the risk register if it reveals a new risk class | Dhanush | Within the same work session |

## 6. The kill switch

| Question | Answer |
|---|---|
| What is the mechanism? | Setting `CONFIDENCE_THRESHOLD=1.1` in `.env` (impossible to reach — forces every ticket to escalate) or stopping the API process. A dedicated `KILL_SWITCH=true` env check that short-circuits `route()` straight to escalate is the cleaner version — see the PRD revision log for whether this was added. |
| Who is authorised to use it? | Dhanush (solo project; in a real deployment, Marcus as Head of Support) |
| How long until it takes effect? | Immediate for new requests; a config change + restart (seconds to low minutes) |
| What happens to tickets in flight? | Each ticket is processed synchronously start-to-finish in one call; there is no queued/async state to worry about losing mid-flight |
| How is it tested? | `tests/test_route.py` covers the always-escalate paths; a dedicated kill-switch test is added once the explicit flag exists |

## 7. The declaration

| Statement | Position |
|---|---|
| This system must never ... | Auto-send a response containing private customer data, an unsupported factual claim, or a commitment about refunds/pricing/timelines |
| The mechanism that enforces that is ... | The `validate` stage's five guardrails, which run on every response before release and can block it — not advisory checks |
| The most likely way it could still cause harm is ... | A private-data pattern the regex guardrails weren't written to catch (R-09), or a genuinely ambiguous ticket where the classifier is confidently wrong and the calibration check doesn't catch it in time |
| We would not deploy this without first ... | Running the fairness audit against real evaluation results (not just the dev-set signal), tightening the private-data guardrail beyond regex (e.g. a dedicated PII-detection pass), and getting a second human reviewer on the hallucination-rate sample per the Evaluation Framework |
