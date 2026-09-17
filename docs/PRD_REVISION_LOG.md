# PRD Revision Log (Stage 5)

Recorded once real evaluation results existed to test version one's assumptions
against, per the compulsory Stage 5 revision. Source: `docs/workbooks/Stage_5_PRD_Revision_Log.docx`,
this file is a markdown rendering of that workbook's content so the two
references to it in `docs/PRD.md` and `README.md` resolve to something.

## Revision summary

| | |
|---|---|
| New version number | 2.0 |
| Date of revision | 2026-09-16 |
| Who carried out the revision | Dhanush Varanasi |
| Who reviewed and agreed it | Dhanush Varanasi (solo project; every change below is driven by the evaluation harness's own live output on real runs, not a stakeholder negotiation) |
| Requirements changed | 4 |
| Requirements added | 1 |
| Requirements removed | 0 |

## The changes

| Requirement | v1 said | v2 says | What prompted it |
|---|---|---|---|
| FR-02 / FR-03 | Classify and generate using `llama-3.3-70b-versatile` (generation) and `llama-3.1-8b-instant` (classification) | `openai/gpt-oss-120b` (generation) and `openai/gpt-oss-20b` (classification) | Groq retired both original models on 2026-08-16. Every live call returned an error and every ticket fell back to `unclear_request` with forced escalation, the A11 graceful-degradation path working exactly as designed, just against a stale model ID. Traced to Groq's own deprecation page and fixed in commit `7ef1a6e`. |
| NFR-01 (latency, p95 under 3s) | p95 under 3 seconds, unqualified | p95 under 3 seconds under normal provider conditions; tenacity retry backoff (up to 60s per retried call) is the documented dominant driver of tail latency under rate limiting, not the pipeline's own processing | Two live runs measured p95 well above target: 10.35s on the 500-ticket run, 4.08s on the 80-ticket rerun. Both contaminated by retry sleeps from the same daily quota problem (assumption 2 below). |
| Success measures (FCR, escalation rate) | FCR 60%+ against a 42% baseline; escalation 30% or lower against a 58% baseline | Targets retained as the aspirational numbers for a fully available provider. The two live measurements (27.6% FCR / 500 tickets, 5.0% FCR / 80 tickets) are reframed as provider-availability samples, not accuracy samples. The calibration table's 0.8-1.0 confidence band, 90.4% observed accuracy on the 500-ticket run, is the better current estimate of real model quality | Two consecutive live runs both dominated by Groq's tokens-per-day limit (161/500 and 72/80 tickets fell back with stated confidence 0.0), which forces escalation regardless of what the classifier would otherwise have said. |
| Governance risk R-06 (model provider unavailable) | Likelihood Medium, on the free tier | Likelihood reclassified to confirmed and recurring for any single-day evaluation run above roughly 150-200 tickets on this account. Materialised twice in one day: once as a model deprecation, once as tokens-per-day exhaustion | Direct observation on 2026-09-16: the deprecation incident that morning, then the 500-ticket run exhausting the day's quota by roughly ticket 160, then the 80-ticket rerun an hour later starting with under 1% of that quota left. |
| NFR-08 (new): metrics reporting must separate provider availability from model accuracy | Did not exist in v1 | Every `metrics_report.json` must make it possible to tell a wrong classification apart from a classification the model never got to make. The calibration table's per-band n, stated_confidence and observed_accuracy already does this (`stated_confidence: 0.0` with `used_fallback: true` is the fallback path's signature), so this formalises what the harness already produced | Without this distinction, a reader of the headline FCR/escalation numbers alone would reasonably conclude the classifier is unreliable, when the 500-ticket run's clean subset shows 90.4% accuracy. |

A related fix, not a numbered requirement change but recorded in the same
revision: the decision log coverage check (`governance.decision_log_reconciles`)
was rescoped from comparing a run against the whole accumulating decisions
database to comparing it against only that run's own ticket IDs
(`count_distinct_tickets_in()`), with two regression tests added. See
`docs/governance.md` section 1 for the full bug narrative.

## Assumptions that turned out to be wrong

| Assumption | Held? | What was found | What changed |
|---|---|---|---|
| The hidden 120-ticket evaluation set uses the identical schema to `validation_tickets.json` | Not yet testable | The hidden set is never available before grading. Indirect evidence is positive: ingest handled both known ticket sets cleanly, and normalized tickets tolerate missing or malformed fields by construction | No change to ingest; flagged to re-verify at grading time. |
| Groq's free tier has sufficient throughput for a 100-500 ticket unattended run | No, clearly wrong | The 200,000-token daily cap, shared across both models, is exhausted well before 500 tickets complete. 161/500 (32.2%) fell back on the main run; the 80-ticket rerun an hour later started with 199,586/200,000 tokens already spent, so 72/80 (90%) fell back immediately | NFR-01 caveated, NFR-08 added; this is the headline technical finding of this revision. Practical mitigation: split any run above roughly 100-150 tickets across two calendar days, or run a small canary first. |
| The 29-article documentation corpus is representative of what a correct answer needs | Yes, strongly | Retrieval hit rate was 94.4% (500-ticket run) and 96.23% (80-ticket run), both unaffected by the Groq quota problem since retrieval runs locally through sentence-transformers/Chroma, not through Groq's API | None needed; the chunking and embedding approach is validated as-is. |
| Non-fluent-English tickets can be reasonably served by an English-only generator in v1 | Provisionally yes, on the cleaner sample | 500-ticket run: 28.33% resolution for non-fluent (n=120) vs 27.37% for fluent (n=380), under 1 point apart, well inside the 5-point NFR-07 threshold. The 80-ticket rerun's fluency split (10.53% vs 3.28%) isn't usable as corroboration since ~90% of that run was fallback-driven noise | No PRD change for v1. Flagged that a full clean run is still needed before treating this as settled for a real deployment recommendation. |

## What was decided not to change

| Looked wrong | Why it was left | What it would have cost |
|---|---|---|
| The confidence threshold (0.80), given escalation rates of 72.4% and then 95% | `router.py` checks `classification.used_fallback` and forces escalate before the confidence threshold is ever evaluated; every fallback ticket already has confidence 0.0, so lowering the threshold would not have touched any ticket that actually drove the high escalation rate, only the 0.8-1.0 band, which was already resolving well (90.4% observed accuracy) | No real cost either way, so changing it now would be guessing rather than acting on evidence. Revisit after a full clean run, to see whether the healthy band's calibration gap (currently 0.013) suggests moving it. |
| Groq as the model provider, given it caused both the deprecation incident and the quota exhaustion | NFR-03 constrains the project to a free tier; the pipeline's prompts and control flow are provider-agnostic, so switching later is a config change, not a redesign. Groq was also chosen specifically for inference speed relevant to NFR-01 | Switching providers or paying for a higher tier is outside this project's budget and timeline constraint. Revisit only for a real deployment beyond this capstone. |
| A resumable/checkpointed harness that could pick up a quota-exhausted run where it left off | The graceful-degradation behaviour under a real failure is itself one of the two headline pieces of evidence in this report, not only an obstacle to route around | Roughly 3-4 hours of state-management work, not budgeted in the remaining days before submission. |

## Reflection

**What was most misunderstood about the problem when v1 was written:** how load-bearing the model provider's real-world constraints would be, first a deprecated model ID, then a daily token quota, compared with the model's actual classification quality. NFR-02 (graceful degradation) was written as a theoretical spec to satisfy with a fallback branch and a unit test; it turned out to be the single most load-bearing thing in the whole build, firing for real, unprompted, twice in one day, and producing the two headline pieces of evidence in this report.

**What would have caught it earlier:** nothing in the stakeholder interviews or the ticket dataset, since it's an infrastructure constraint of the specific free-tier provider chosen during the build, not a requirements-gathering gap. The closer catch would have been reading Groq's own rate-limit documentation before committing to a same-day, unattended, 500-ticket run immediately after a smaller validation run had already used part of the daily budget.

**What would be done differently starting again on Monday:** run the evaluation harness on a small, 10-20 ticket canary first thing each session to check the day's remaining quota headroom before committing to a large unattended run, and split any run above roughly 100 tickets across two calendar days from the start.

**What is still uncertain:** the true classification accuracy, FCR and escalation rate under normal, non-quota-exhausted operating conditions are not conclusively measured at n=500. The calibration table's 0.8-1.0 confidence band (334/500 real classifications) shows 90.4% observed accuracy, the best current estimate, but a full clean run split across two calendar days would be needed to state a single trustworthy topline FCR number with confidence.


## Addendum: a clean run, 2026-09-17

Everything above was written on 2026-09-16, from two evaluation runs that both
turned out to be dominated by Groq's daily token quota rather than a clean
read of the system. On 2026-09-17, once that quota had reset, the 80-ticket
validation set was rerun through the unchanged pipeline. This addendum
records what that run changed. Source: `evaluation/results/validation_run/metrics_report.json`,
overwritten in place by this run.

### What the clean run showed

| Question the 2026-09-16 log left open | What the 2026-09-17 clean run showed |
|---|---|
| True FCR and escalation rate under normal, non-quota-exhausted conditions, not conclusively measured at any scale | 81.25% FCR, 18.75% escalation, both clearing the PRD's 60%+/30%-or-lower targets. Zero of the 80 tickets used the fallback path, confirmed directly from the per-ticket results file. |
| Whether tenacity's retry backoff was the dominant driver of tail latency under rate limiting | No. The clean run had zero fallbacks and therefore zero rate-limit retries, yet its p95 (10.64s) is close to the 500-ticket run's (10.35s) and far above the quota-exhausted run's (4.08s). Splitting this run's own latencies by final action shows why: escalated tickets (one classify call) had a median of 0.80s; auto-responded tickets (which also call the generator) had a median of 7.52s and a max of 11.16s. The generation call's own inference time on Groq's free tier is the dominant driver, not retry backoff. |
| Whether the decision log reconciliation fix (see the governance-bug note above) produces a clean governance figure in a real run | Yes. This run's governance block shows `decisions_logged: 80` against 80 tickets processed, `decision_log_reconciles: true`, the first live run in this project to report a post-fix governance figure rather than being verified only by regression tests. |
| Classification accuracy under clean conditions | 90% overall, and the 0.8-1.0 calibration band (78 of 80 tickets) shows stated confidence 0.92 against observed accuracy 0.91, a 0.01 gap, in close agreement with the 500-ticket run's 0.013 gap on 334 tickets. |
| Retrieval hit rate under clean conditions | 96.23%, identical to the quota-exhausted run's figure on the same 53 checkable tickets, confirming retrieval's independence from the Groq quota problem. |
| Fairness, by language fluency | Confirmed in the same direction as the 500-ticket run: non-fluent tickets resolved at 84.21% (n=19) against 80.33% for fluent (n=61), again better rather than worse, well inside the 5-point NFR-07 threshold. This finding is now corroborated by two independent clean-enough samples. |
| Fairness, by customer tier | Not confirmed, reopened instead. Business resolved at 93.33% (n=30), enterprise at 87.5% (n=8), standard at only 71.43% (n=42), a 21.9-point spread, well outside the 5-point threshold the 500-ticket run's much larger segments (n=164 to n=253, 3.40-point spread) comfortably met. At n=30 to n=42 this could be noise, but it cannot be dismissed on that basis alone either. This question was treated as settled after the 500-ticket run and is not settled any longer. |

### What this changes going forward

NFR-01's qualification (p95 under 3 seconds under normal provider conditions)
is now understood differently than it was on 2026-09-16: the dominant driver
of tail latency is the generation call's own inference time on Groq's free
tier, not retry backoff under rate limiting. Retry backoff still contributes
when it is present, but a genuinely clean run misses the 3-second target by
roughly the same margin as a quota-contaminated one, which retry backoff
alone cannot explain.

The success-measures reframing holds, but is now backed by a direct
measurement rather than only the calibration table's inference: the 60%+/30%-
or-lower targets have been met once, at validation-set (80-ticket) scale.
Whether they hold at the 500-ticket development-set scale remains the
outstanding question, unchanged from the original revision's recommendation
to run a full clean evaluation split across two calendar days.

The tier-fairness question is reopened, not closed. A full clean run at
development-set scale, the same run already recommended for the FCR question
above, would also settle whether the 21.9-point spread seen here is a real
effect or an artifact of small segments.

### Reflection, updated

**What is still uncertain**, revised from the original: the true
classification accuracy, FCR and escalation rate under normal,
non-quota-exhausted conditions are now measured, directly, at validation-set
scale (this addendum). What remains genuinely open is whether that holds at
the 500-ticket development-set scale, and whether the tier-fairness spread
surfaced by this same clean run is real. Both would be settled by the same
next step: a full clean run of the 500-ticket set, split across two calendar
days to avoid the quota ceiling.
