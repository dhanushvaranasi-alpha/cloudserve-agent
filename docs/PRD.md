# Product Requirements Document — v1
## CloudServe Solutions support automation system

**Status:** Draft v1 (to be revised once build/evaluation surfaces what this got wrong — Stage 5 is compulsory)
**Author:** Dhanush Varanasi
**Date:** 2026-09-16

---

## 1. Document control

| | |
|---|---|
| Version | 1.0 |
| Prepared by | Dhanush Varanasi |
| Traceability | Every FR/NFR below cites the discovery evidence (interview quote or dataset statistic) that produced it |
| Revision | See `docs/PRD_REVISION_LOG.md` (Stage 5) once the build surfaces what v1 got wrong |

## 2. The problem

CloudServe asked for a chatbot. The discovery evidence says that isn't what would actually fix their support function.

**What's actually going wrong**, evidenced from the five stakeholder interviews and `development_tickets.json` (n=500):

1. **The documentation isn't the gap — findability is.** 71.4% of tickets are `answerable_from_docs`. Ines (technical writer): "internally, I do not think the support team uses [the docs] at all... our internal search matches on titles and exact terms, and customers do not describe problems in the words I used for the title." Sofia (Tier 1): "The documentation is fine. I cannot find things in it."
2. **Escalations don't need to cost what they cost.** Of tickets that were historically escalated, 49% (138/281) were themselves `answerable_from_docs`. This is the data-backed version of Daniel's (Tier 2) claim that Marcus doesn't see: "Marcus sees the escalation rate, not what is inside the escalations... a good number were answered by pasting a link and two sentences. That is not a tier two problem, that is a confidence problem or a findability problem."
3. **Escalations arrive with no context.** Daniel: "Usually just the original ticket, forwarded. No summary, no note about what was already tried... If an escalation arrived saying here is the ticket, here is what I think it is about, here is the documentation that seemed relevant, and here is specifically what I was not confident about, I would be twice as fast."
4. **Non-fluent-English tickets are a real, if not yet fully quantified, fairness risk.** Sofia flagged it directly from experience; the dev-set historical numbers are mixed (not a clean gap), but the Governance Framework independently names retrieval-on-phrasing as a known failure mode for non-fluent tickets. Treated here as a hypothesis the fairness audit must test against what *this system* does, not as an already-proven historical fact.

**Problem statement:** CloudServe's support failure is a findability and confidence problem, not a documentation or headcount shortage. A system that grounds answers in the real documentation with calibrated confidence, auto-resolves what it can defend, and escalates the rest *with a draft, sources, and its own stated uncertainty attached* addresses what Sofia and Daniel actually described — not just Marcus's response-time number.

## 3. User groups

| Group | Represented by | What they need from this system |
|---|---|---|
| Tier 1 agents | Sofia Restrepo | Fewer tickets needing a from-scratch answer; when one does reach them, less time spent searching |
| Tier 2 engineers | Daniel Okonkwo | Escalations that arrive with a summary, retrieved sources, and an explicit statement of what the system wasn't sure about |
| Head of Support | Marcus Adeyemi | Response time and FCR to move; an explainable system ahead of an autumn compliance review; zero confidently-wrong answers to customers |
| Customers | Ravi Menon | Fast answers when the question is low-stakes; honesty about what's automated; no false certainty on anything he'd act on |
| Technical writer | Ines Varga | Visibility into which article an answer came from, so a wrong answer can be traced to "article is wrong" vs. "system misread it" |

## 4. Functional requirements

Each row: requirement → discovery evidence it's traceable to → where it's implemented.

| ID | Requirement | Evidence | Implementation |
|---|---|---|---|
| FR-01 | Ingest tickets from all four channels (email, chat, docs_comment, forum) into one normalized representation, preserving original text and channel | Build Spec A2; four channels behave differently per brief §01 | `src/ingest/ingest.py` |
| FR-02 | Classify every ticket against the real 22-class intent taxonomy | `Dataset_Guide.docx` schema; classes are near-uniformly distributed (2.6–5.8% each), so no shortlist is defensible | `src/classify/classifier.py`, `prompts/PR-01_classification.md` |
| FR-03 | Attach a calibrated confidence score (and up to 2 alternatives considered) to every classification | Build Spec A3; Evaluation Framework's calibration requirement | `src/classify/classifier.py` |
| FR-04 | Retrieve ranked, identifiable documentation passages; return nothing rather than something irrelevant | Ines: search is the actual failure, not doc coverage; Build Spec A4 | `src/retrieve/retriever.py` (heading-aware chunking — see architecture doc) |
| FR-05 | Generate answers grounded only in retrieved passages, never outside knowledge | Marcus: "I would rather it said nothing than said something wrong" | `src/generate/generator.py`, `prompts/PR-02_generation.md` |
| FR-06 | Attach citations to generated claims; citations must resolve to passages actually retrieved | Ines wants to know which article an answer came from; Build Spec A6 | `src/validate/guardrails.py::grounding_guardrail` |
| FR-07 | Decline explicitly ("I don't know") when ungrounded, rather than filling the gap | Ravi: acceptable only "provided it is honest" | `src/generate/generator.py` |
| FR-08 | Route deterministically: same input → same decision. Always-escalate for `compliance_request`, `security_incident`, `feature_request`, `unclear_request` regardless of confidence; otherwise threshold-based | Daniel names security/billing/data-location as never-automate; data shows these 4 classes are escalated 100% of the time historically (87/500 tickets); Build Spec A5 | `src/route/router.py` |
| FR-09 | Validate every generated response against 5 blocking guardrails before release: private data, grounding, instruction integrity, tone/scope, confidence floor | Governance_Framework.docx §4; Build Spec A7 | `src/validate/guardrails.py` |
| FR-10 | Log every automated decision (classification, retrieval, routing, generation, validation) with the minimum record schema | Governance_Framework.docx §1; Marcus's compliance review | `src/persistence/decision_log.py` |
| FR-11 | Escalations carry the drafted summary, retrieved sources, and the specific point of low confidence to the human agent | Daniel: "I need it to show its working" — this is the core design response to the discovery finding | `src/pipeline.py::run_ticket` (`escalation_summary`) |
| FR-12 | Evaluation harness accepts `--input`/`--output` paths (never a hardcoded file) and processes a full ticket set unattended, producing a metrics report with no manual post-processing | Build Spec A9/A10 — the hidden 120-ticket set is never seen before grading | `evaluation/harness.py` |

## 5. Non-functional requirements

| ID | Requirement | Target | Source |
|---|---|---|---|
| NFR-01 | Response latency, 95th percentile | Under 3 seconds | Evaluation Framework |
| NFR-02 | Availability / graceful degradation under provider outage, rate limiting, timeout, malformed input | No crash; continues, escalates | Build Spec A11 |
| NFR-03 | Cost | Free tier only (Groq) | Project Instructions §Cost |
| NFR-04 | No credentials in source or history | Zero occurrences | Build Spec, Submission Guide |
| NFR-05 | Determinism | Same ticket → same routing decision on re-run | Build Spec A5 |
| NFR-06 | Private data in outbound responses | Zero occurrences, no acceptable rate | Governance conditions |
| NFR-07 | Cross-group quality variation | Under 5 percentage points | Governance conditions |

## 6. Out of scope (v1)

Explicit cuts, each defensible on its own terms rather than only "ran out of time":

- **Proactive/outbound outreach** — the discovery evidence is entirely about inbound tickets; nothing supports building outbound flows.
- **A full agent-facing review UI** — Tier 2 gets escalation context via the decision log and API response, not a dedicated desk application. The *content* Daniel asked for (summary, sources, confidence) is in scope; the UI to browse it is not.
- **Non-English generation** — answers are produced in English even for non-fluent-English tickets in v1. This is a named limitation, not a silent one: it is exactly the mechanism the fairness audit tests, and a candidate PRD revision if the audit finds a real gap.
- **Auto-updating the documentation corpus** — the 29 articles are treated as a fixed snapshot; a staleness-detection pipeline (Ines mentioned a review rotation) is future work.
- **Fine-tuning or hosting a custom model** — the free hosted Groq tier is used as-is, consistent with the project's cost constraint.
- **Payment/refund/contractual actions** — explicitly blocked by the tone_and_scope guardrail, never a build target.

## 7. Assumptions and what happens if they're wrong

| Assumption | If wrong |
|---|---|
| The hidden 120-ticket evaluation set uses the identical schema to `validation_tickets.json` (as the pack states) | Harness would need a schema-compatibility fix — mitigated by ingest already tolerating missing/malformed fields (FR-01) |
| Groq's free tier has sufficient throughput/rate limits to process ~100-500 tickets in one unattended run | Mitigated by retry/backoff (`tenacity`) and A11 graceful degradation; if genuinely insufficient, this is a documented, reportable finding, not a silent failure |
| The 29-article documentation corpus is representative of what a correct answer needs (per `ground_truth_responses.json`'s `expected_doc_ids`) | If retrieval hit-rate is low against ground truth, that's a chunking/embedding decision to revisit, tracked as a candidate PRD revision |
| Non-fluent-English tickets can be reasonably served by an English-only generator in v1 | If the fairness audit shows a real gap, this becomes the headline finding of the PRD revision (Stage 5) |

## 8. Success measures

Reused directly from `Evaluation_Framework.docx` so the PRD and the evaluation report speak the same language:

| Measure | Baseline | Target |
|---|---|---|
| First contact resolution | 42% | 60%+ |
| Escalation rate | 58% | 30% or lower |
| Classification precision | — | 85%+ |
| Citation accuracy | — | 95%+ |
| Latency, p95 | — | Under 3s |
| Private data occurrences | — | Zero |
| Cross-group variation | — | Under 5 points |
| Confidence calibration gap | — | Within 5 points |

---
*This is v1, written before the build. Stage 5 (compulsory revision) records what changed once real evaluation results exist.*
