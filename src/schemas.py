"""
Core data types shared across the pipeline.

These mirror the ticket/documentation schema in 05_Datasets/Dataset_Guide.docx
so that ingest can validate incoming tickets against it directly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Channel(str, Enum):
    EMAIL = "email"
    CHAT = "chat"
    DOCS_COMMENT = "docs_comment"
    FORUM = "forum"


class CustomerTier(str, Enum):
    ENTERPRISE = "enterprise"
    BUSINESS = "business"
    STANDARD = "standard"


class LanguageFluency(str, Enum):
    FLUENT = "fluent"
    NON_FLUENT = "non_fluent"


class Urgency(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ActionTaken(str, Enum):
    AUTO_RESPOND = "auto_respond"
    ESCALATE = "escalate"
    BLOCK = "block"


# The 22 intent classes, taken from the Dataset Guide / observed in
# development_tickets.json. Intents outside this set fall back to
# "unclear_request".
INTENT_CLASSES = [
    "account_access",
    "api_key_issue",
    "api_usage_question",
    "authentication_failure",
    "billing_query",
    "compliance_request",
    "configuration_help",
    "data_export",
    "data_residency",
    "database_issue",
    "deployment_failure",
    "feature_request",
    "integration_help",
    "onboarding",
    "performance_degradation",
    "quota_or_overage",
    "rate_limit",
    "rollback_request",
    "security_incident",
    "sso_configuration",
    "unclear_request",
    "webhook_issue",
]

# Intents that must never be auto-answered, regardless of confidence.
# Backed by discovery evidence: in development_tickets.json these four
# classes are must_not_auto_respond=True in 100% of instances (87/500
# tickets), and they map directly onto what Daniel (Tier 2) and Marcus
# (Head of Support) independently flagged as never-automate categories
# (security/compliance) plus the two classes with zero documentation
# coverage (feature_request, unclear_request never resolve from docs.json).
ALWAYS_ESCALATE_INTENTS = {
    "compliance_request",
    "security_incident",
    "feature_request",
    "unclear_request",
}


class RawTicket(BaseModel):
    """A ticket as it arrives, matching the pack's ticket schema."""

    ticket_id: str
    channel: Channel
    subject: str = ""
    body: str
    received_at: datetime
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_tier: Optional[CustomerTier] = None
    customer_region: Optional[str] = None
    language_fluency: Optional[LanguageFluency] = None

    # Present in development/validation/hidden files for scoring purposes.
    # The pipeline must NOT read these when making its own decisions --
    # they exist so the evaluation harness can grade the system's output
    # against ground truth after the fact.
    labels: Optional[dict] = None
    history: Optional[dict] = None


class NormalizedTicket(BaseModel):
    """Ingest's output: one shape regardless of source channel."""

    ticket_id: str
    channel: Channel
    original_subject: str
    original_body: str
    text: str  # subject + body, cleaned, what downstream components read
    received_at: datetime
    customer_tier: CustomerTier = CustomerTier.STANDARD
    customer_region: str = "unknown"
    language_fluency: LanguageFluency = LanguageFluency.FLUENT
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ClassificationResult(BaseModel):
    intent: str
    urgency: Urgency
    confidence: float  # 0..1, calibrated probability the intent is correct
    alternatives: list[dict] = Field(default_factory=list)  # [{"intent":.., "confidence":..}]
    used_fallback: bool = False


class RetrievedPassage(BaseModel):
    doc_id: str
    title: str
    chunk_text: str
    score: float


class RetrievalResult(BaseModel):
    passages: list[RetrievedPassage] = Field(default_factory=list)
    query: str = ""

    @property
    def has_grounding(self) -> bool:
        return len(self.passages) > 0


class RoutingDecision(BaseModel):
    action: ActionTaken
    reason: str
    threshold_applied: float
    forced: bool = False  # True if an always-escalate rule fired (not the threshold)


class GenerationResult(BaseModel):
    answer_text: Optional[str]  # None if the model declined to answer
    citations: list[str] = Field(default_factory=list)  # doc_ids actually cited
    declined: bool = False
    declined_reason: Optional[str] = None


class GuardrailFinding(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class ValidationResult(BaseModel):
    blocked: bool
    findings: list[GuardrailFinding] = Field(default_factory=list)

    @property
    def blocking_findings(self) -> list[GuardrailFinding]:
        return [f for f in self.findings if not f.passed]


class PipelineResult(BaseModel):
    """What the pipeline returns for one ticket, and what the harness scores."""

    ticket_id: str
    channel: Channel
    classification: ClassificationResult
    retrieval: RetrievalResult
    routing: RoutingDecision
    generation: Optional[GenerationResult] = None
    validation: Optional[ValidationResult] = None
    final_action: ActionTaken
    final_text_sent: Optional[str] = None
    escalation_summary: Optional[str] = None
    error: Optional[str] = None
    latency_seconds: float = 0.0
