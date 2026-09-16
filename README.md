# CloudServe Solutions — support automation system

Forward Deployed AI Engineering capstone. Full brief: `docs/PRD.md` and
`docs/architecture.md`. This README is what the grading process actually
follows (Build Specification, "how it will be tested") — every command
below has been run from a clean checkout.

## What this is

CloudServe asked for a chatbot. Discovery evidence (see `docs/PRD.md` §2)
showed the real problem is findability and confidence, not a documentation
or headcount shortage. This system retrieves grounded answers from
CloudServe's real documentation, auto-resolves what it can defend with a
calibrated confidence score, and escalates the rest **with the draft,
sources, and its own stated uncertainty attached** — rather than a bare
forwarded ticket.

## Setup

Requires Python 3.10+ and a free [Groq](https://console.groq.com) API key.

```bash
git clone <this repo> && cd cloudserve-support
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set GROQ_API_KEY=<your key>
```

The first run downloads the `all-MiniLM-L6-v2` embedding model (~90MB,
one-time, cached under `~/.cache` afterward) and indexes the 29
documentation articles into a local Chroma store under `storage/chroma/` —
this needs internet access once, even though everything after that runs
locally.

Data files (`development_tickets.json`, `validation_tickets.json`,
`documentation.json`, `ground_truth_responses.json`) are expected under
`data/`.

## Running it

**Start the API:**
```bash
uvicorn src.api.main:app --reload --port 8000
# POST a ticket:
curl -X POST http://localhost:8000/tickets -H "Content-Type: application/json" -d '{
  "ticket_id": "DEMO-1", "channel": "email",
  "subject": "Cannot log in to the console",
  "body": "I keep getting an invalid credentials error when I try to sign in."
}'
```

**Run the full evaluation set, unattended (the gate — Build Spec A9/A10):**
```bash
python -m evaluation.harness --input data/validation_tickets.json --output evaluation/results/
```
This is the exact command that will be run against the hidden 120-ticket
set after submission, with `--input` pointed at a file this system has
never seen. It takes an input path and an output path as arguments —
never a hardcoded filename — for exactly that reason. Output:
`evaluation/results/metrics_report.json` and `per_ticket_results.jsonl`.

**Run the tests:**
```bash
python -m pytest tests/ -v
```
All 51 tests run with **no network access and no API key** — every
component that calls an external service (`src/llm_client.py`'s
`GroqChatClient`, `src/retrieve/retriever.py`'s `Retriever`) has a fake
counterpart (`FakeChatClient`, `FakeRetriever`) used throughout `tests/`.
This is also how the pipeline logic was verified during development in an
environment with no outbound network access at all — see
`docs/architecture.md` §2.

## Repository structure

```
src/
  ingest/       normalise tickets from 4 channels          (A2)
  classify/     intent + urgency + calibrated confidence   (A3)
  retrieve/     grounded passage search over documentation  (A4)
  route/        deterministic auto_respond / escalate       (A5)
  generate/     grounded answer + citations, or decline     (A6, A7)
  validate/     5 blocking guardrails                       (A7)
  persistence/  decision log (SQLite)                       (A8)
  api/          FastAPI interface
  pipeline.py   wires all six components together
  llm_client.py Groq wrapper + retry/backoff + fakes         (A11)
  config.py     all settings, read from .env
  schemas.py    shared pydantic types
prompts/        versioned prompt library (Stage 3)
evaluation/     harness (A9/A10) + metrics calculations
tests/          51 tests, no network required
docs/           PRD, architecture notes, revision log
data/           sample ticket/documentation data
.github/workflows/  CI: tests + credential scan on every push
```

## Design decisions worth knowing before reading the code

- **Dependency pins were updated** from the pack's template
  `requirements.txt` (which pins `chromadb==0.3.21`, `langchain==0.1.0`,
  etc. — these no longer install). Current stable versions are used
  instead; everything is still free/open source. See
  `docs/architecture.md` §5 for the full list and reasoning.
- **No LangChain/LangGraph.** The pipeline is six plain Python functions
  called in sequence with branches — see `src/pipeline.py`. This was a
  deliberate choice for a project this size: easier to unit-test with
  fakes, easier to explain on the video line-by-line.
- **The confidence threshold (0.80) is the pack's own illustrative
  number, not a measured one.** `evaluation/harness.py`'s calibration
  table is what should justify moving it — see `docs/PRD_REVISION_LOG.md`
  once real evaluation results exist.

## Known limitations (stated plainly, not left for you to notice)

See `docs/PRD.md` §6 "Out of scope" for the full list. Headline ones:
generation is English-only even for non-fluent-English tickets in v1 (a
named fairness risk under active test, not a silent gap); there's no
agent-facing review UI, only the API + decision log.

## AI tool use declaration

See the report (`docs/report.pdf` once written) for the full declaration.
Short version: Claude (Anthropic) was used throughout for code
implementation, test writing, and drafting reference documents (PRD,
architecture notes), under continuous review and correction. Discovery
findings, the problem statement, evaluation interpretation and the
reflection section are the author's own, per the project's AI-use rules.
