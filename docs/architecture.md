# Architecture — CloudServe support automation system

## 1. High-level view: six components, three cross-cutting concerns

```
                    ┌─────────────────────────────────────────────┐
                    │        Cross-cutting concerns                │
                    │  1. Decision logging & auditability          │
                    │  2. Guardrails & safety                      │
                    │  3. Reliability & observability (retry, lat.)│
                    └─────────────────────────────────────────────┘
                                        │  (each component below writes
                                        │   to the decision log; guardrails
                                        │   sit before release; retries wrap
                                        │   every model call)
                                        ▼
  Ticket ──▶ INGEST ──▶ CLASSIFY ──▶ RETRIEVE ──▶ ROUTE ──┬──▶ GENERATE ──▶ VALIDATE ──▶ sent to customer
  (4 channels)                                            │
                                                           └──▶ ESCALATE (with draft context) ──▶ human agent
```

| Component | Responsibility | Failure mode designed against | Code |
|---|---|---|---|
| Ingest | Normalise 4 channels into one shape; preserve original text + channel | Channel quirks leaking downstream | `src/ingest/ingest.py` |
| Classify | Intent (22 classes) + urgency + calibrated confidence + alternatives | Confidence that doesn't mean probability | `src/classify/classifier.py` |
| Retrieve | Ranked, identifiable passages from the 29-doc corpus | Retrieving something plausible-but-irrelevant | `src/retrieve/retriever.py` |
| Route | Deterministic auto_respond vs. escalate decision, logged with reason | Threshold chosen by feel, not measurement | `src/route/router.py` |
| Generate | Grounded answer + citations, or explicit decline | Fluent text unsupported by sources | `src/generate/generator.py` |
| Validate | 5 blocking guardrails before any response is released | A guardrail that only warns | `src/validate/guardrails.py` |

## 2. Low-level view: three layers

```
┌──────────────────────────────────────────────────────────┐
│  Interface layer                                          │
│  src/api/main.py (FastAPI)   evaluation/harness.py (CLI)  │
├──────────────────────────────────────────────────────────┤
│  Orchestration / business-logic layer                     │
│  src/pipeline.py  ──  src/{classify,retrieve,route,        │
│                        generate,validate}/*.py             │
├──────────────────────────────────────────────────────────┤
│  Persistence / integration layer                           │
│  src/persistence/decision_log.py (SQLite)                  │
│  src/retrieve/retriever.py's Chroma client (vector store)  │
│  src/llm_client.py (Groq, swappable via ChatClient Protocol)│
└──────────────────────────────────────────────────────────┘
```

Separating these is what makes the system testable without a network
connection: `ChatClient` and the retriever are both structural interfaces
(`FakeChatClient`, `FakeRetriever` in the same modules), so
`tests/test_pipeline.py` exercises real classify → route → validate logic
with zero external calls. The only thing that changes to go from "tests"
to "the real system" is which concrete object gets passed in — nothing in
`src/pipeline.py` changes.

## 3. How the system decides (routing)

```
                     ┌─────────────────────────────┐
ticket ──classify──▶ │ used_fallback (provider down │──yes──▶ ESCALATE (forced)
                     │  / unparseable response)?    │
                     └──────────────┬────────────────┘
                                   no
                                    ▼
                     ┌─────────────────────────────┐
                     │ intent in ALWAYS_ESCALATE_   │──yes──▶ ESCALATE (forced)
                     │ INTENTS (compliance, security,│
                     │ feature_request, unclear)?    │
                     └──────────────┬────────────────┘
                                   no
                                    ▼
                     ┌─────────────────────────────┐
                     │ retrieval found a passage    │──no───▶ ESCALATE (forced)
                     │ above the relevance threshold?│
                     └──────────────┬────────────────┘
                                   yes
                                    ▼
                     ┌─────────────────────────────┐
                     │ confidence ≥ CONFIDENCE_      │──no───▶ ESCALATE
                     │ THRESHOLD (default 0.80)?     │
                     └──────────────┬────────────────┘
                                   yes
                                    ▼
                            AUTO_RESPOND ──generate──▶ VALIDATE ──blocked?──yes──▶ ESCALATE
                                                                         │no
                                                                         ▼
                                                                  sent to customer
```

Every branch is logged with a human-readable reason (`src/route/router.py`),
per Build Spec A5's "a support manager could read it" requirement.

### Threshold selection

`CONFIDENCE_THRESHOLD=0.80` in `.env.example` is the pack's own illustrative
number, not a measured one. The honest position for v1: it is a placeholder
until `evaluation/harness.py`'s calibration table (bins predicted confidence
against observed accuracy, per `Evaluation_Framework.docx`) is run against
real classifier output. Moving this number based on that table, and
recording why, is exactly the kind of change the compulsory PRD revision
(Stage 5) is for — it should not be tuned by feel before that data exists.

## 4. Retrieval: chunking strategy

Documents are split on markdown headings (`##`/`###`) rather than by fixed
character count. `Dataset_Guide.docx` warns explicitly that "splitting
inside a resolution sequence tends to produce passages that retrieve well
but read as incomplete" — heading-aware chunking keeps a numbered
resolution sequence intact as one retrievable unit instead of severing it.
See `src/retrieve/retriever.py::_chunk_document`.

Embeddings: `all-MiniLM-L6-v2` (per the pack's stack notes — small, fast,
adequate for a 29-document corpus). Relevance threshold
(`RETRIEVAL_MIN_SCORE=0.35`) converts Chroma's cosine distance to a
similarity score in `[0, 1]`, and retrieval returns *nothing* rather than
a low-relevance passage when nothing clears it — required so the router's
"no grounding found → escalate" branch actually fires instead of silently
grounding an answer in something irrelevant.

## 5. Stack decisions (deviations from the pack's template, and why)

| Area | Pack's suggestion | What's built | Why |
|---|---|---|---|
| Dependency pins | `requirements.txt` template pins very old releases (`chromadb==0.3.21`, `langchain==0.1.0`) that no longer install cleanly | Current stable pins (`chromadb==0.5.20`, etc.) | The template versions fail to install; current versions are still 100% free/open source, satisfying the actual constraint |
| Orchestration | LangChain + LangGraph named as an option | Plain Python control flow in `src/pipeline.py` | A 6-step linear-with-branches pipeline doesn't need a graph orchestration framework; plain functions are easier to unit-test with fakes and easier to explain line-by-line on the video, which matters more at this scope |
| Model provider | OpenRouter or Groq | Groq (`openai/gpt-oss-120b` for generation, `openai/gpt-oss-20b` for classification) | Free tier, fast inference (relevant for the p95<3s latency target), OpenAI-compatible client |
| Decision log | PostgreSQL or SQLite | SQLite | Pack itself says "fine for the decision log at this scale" |

Model IDs were revised mid-project: Groq deprecated and retired
`llama-3.3-70b-versatile` and `llama-3.1-8b-instant` on 2026-08-16 (see
[Groq's deprecation page](https://console.groq.com/docs/deprecations)),
which surfaced as every call returning 404 and every ticket falling back
to `unclear_request` / forced escalation -- the A11 graceful-degradation
path working exactly as designed, just against a stale config. Groq's own
migration guidance points to `openai/gpt-oss-120b` and `openai/gpt-oss-20b`
respectively, both still free-tier; `config.py` and `.env.example` were
updated accordingly. This is the exact scenario R-06 in `governance.md`
names ("Model provider becomes unavailable") -- the retry/backoff and
`UnavailableChatClient` fallback it credits are what kept the run from
crashing while the config was still wrong, it just took a stale model ID
rather than an outage to trigger it.

## 6. Reliability (A11)

Every model call goes through `src/llm_client.py`, which wraps the Groq
client in `tenacity` retry/backoff (3 attempts, exponential wait). If the
provider is fully unavailable (bad key, network down, sustained failure),
`get_default_client()` returns an `UnavailableChatClient` that raises
immediately — which routes every ticket through the classifier's existing
fallback path (`unclear_request`, confidence 0.0, forced escalate) rather
than crashing the harness. The same run that works with a live key also
completes cleanly with no key at all; it just escalates everything, which
is the correct, safe behaviour for "provider unavailable," not a bug.

## 7. What's explicitly not built (see PRD §6 for the full list and why)

A review/desk UI for Tier 2, non-English generation, auto-refreshing the
documentation corpus, and any fine-tuned/custom model are out of scope —
each is a deliberate v1 boundary, not an oversight.
