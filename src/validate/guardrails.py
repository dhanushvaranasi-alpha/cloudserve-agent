"""
Validate: guardrails that run on every generated response before release.

Build spec / governance requirements:
- Runs on every response, not only in testing (A7)
- Checks at minimum: private data, unsupported claims
- Can block -- a guardrail that only warns is not a guardrail (A7)
- Records what it checked and what it found, whether or not it blocked

Five guardrails, matching Governance_Framework.docx section 4 exactly:
private_data, grounding, instruction_integrity, tone_and_scope, confidence_floor.
The first two are deterministic (regex / substring match against retrieved
text) so they work even if the model provider is down -- a guardrail that
itself depends on a live model call is a weaker guardrail.
"""
from __future__ import annotations

import re

from src.schemas import (
    ClassificationResult,
    GenerationResult,
    GuardrailFinding,
    RetrievalResult,
    ValidationResult,
)

# Deliberately conservative patterns: emails, phone numbers, credit-card-like
# digit runs, API-key-shaped tokens, and "account number"-style phrases.
# False positives here cost an unnecessary escalation; false negatives cost
# a real private-data leak. The trade favours over-blocking.
_PII_PATTERNS = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                       # email address
    re.compile(r"\b(?:\+?\d[\s-]?){9,14}\d\b"),                     # phone-like digit run
    re.compile(r"\b(?:\d[ -]*?){13,16}\b"),                         # card-number-like digit run
    re.compile(r"\b(sk|pk|api|key)[-_][A-Za-z0-9]{12,}\b", re.I),   # API-key-shaped token
    re.compile(r"\baccount\s*(number|no\.?)\s*[:#]?\s*\w+", re.I),
]

_COMMITMENT_PATTERNS = [
    re.compile(r"\b(refund|reimburse|credit your account|waive)\b", re.I),
    re.compile(r"\b(guarantee|promise|will definitely)\b", re.I),
    re.compile(r"\bby (tomorrow|end of (day|week)|\d{1,2}(am|pm))\b", re.I),
]

_INJECTION_MARKERS = [
    "ignore the above", "ignore previous", "disregard the system prompt",
    "you are now", "new instructions:", "act as",
]


def private_data_guardrail(text: str | None) -> GuardrailFinding:
    if not text:
        return GuardrailFinding(name="private_data", passed=True, detail="no text to check")
    for pattern in _PII_PATTERNS:
        match = pattern.search(text)
        if match:
            return GuardrailFinding(
                name="private_data", passed=False,
                detail=f"matched pattern suggesting private data near {match.group(0)[:4]}***",
            )
    return GuardrailFinding(name="private_data", passed=True, detail="no PII pattern matched")


def grounding_guardrail(generation: GenerationResult | None, retrieval: RetrievalResult) -> GuardrailFinding:
    if generation is None or generation.declined or not generation.answer_text:
        return GuardrailFinding(name="grounding", passed=True, detail="no answer to check (declined)")

    retrieved_ids = {p.doc_id for p in retrieval.passages}
    if not generation.citations:
        return GuardrailFinding(name="grounding", passed=False, detail="answer has no citations at all")

    unresolved = [c for c in generation.citations if c not in retrieved_ids]
    if unresolved:
        return GuardrailFinding(
            name="grounding", passed=False,
            detail=f"citation(s) {unresolved} do not resolve to a passage actually retrieved",
        )
    return GuardrailFinding(name="grounding", passed=True, detail="all citations resolve to retrieved passages")


def instruction_integrity_guardrail(ticket_text: str) -> GuardrailFinding:
    lowered = ticket_text.lower()
    hit = next((m for m in _INJECTION_MARKERS if m in lowered), None)
    if hit:
        return GuardrailFinding(
            name="instruction_integrity", passed=False,
            detail=f"ticket text contains an instruction-injection marker: {hit!r}",
        )
    return GuardrailFinding(name="instruction_integrity", passed=True, detail="no injection marker found")


def tone_and_scope_guardrail(text: str | None) -> GuardrailFinding:
    if not text:
        return GuardrailFinding(name="tone_and_scope", passed=True, detail="no text to check")
    for pattern in _COMMITMENT_PATTERNS:
        match = pattern.search(text)
        if match:
            return GuardrailFinding(
                name="tone_and_scope", passed=False,
                detail=f"response makes a commitment outside support scope: {match.group(0)!r}",
            )
    return GuardrailFinding(name="tone_and_scope", passed=True, detail="no out-of-scope commitment found")


def confidence_floor_guardrail(classification: ClassificationResult) -> GuardrailFinding:
    if classification.used_fallback:
        return GuardrailFinding(
            name="confidence_floor", passed=False,
            detail="classification used the fallback path; a missing confidence score is "
                   "treated as a low one, not a high one",
        )
    return GuardrailFinding(name="confidence_floor", passed=True, detail="confidence score present")


def run_guardrails(
    *,
    classification: ClassificationResult,
    retrieval: RetrievalResult,
    generation: GenerationResult | None,
    ticket_text: str,
) -> ValidationResult:
    findings = [
        instruction_integrity_guardrail(ticket_text),
        confidence_floor_guardrail(classification),
        grounding_guardrail(generation, retrieval),
        private_data_guardrail(generation.answer_text if generation else None),
        tone_and_scope_guardrail(generation.answer_text if generation else None),
    ]
    blocked = any(not f.passed for f in findings)
    return ValidationResult(blocked=blocked, findings=findings)
