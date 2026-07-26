"""Strict internal schemas for fact extraction and deterministic decisions."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.agent.enums import AcceptanceDecision, AgentIntent, IssueField, SafetyFlag
from app.domain.enums import IssueCategory

EvidenceText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
FactName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,79}$")]

ARCHITECTURE_ID = "hybrid_interpretation"
ARCHITECTURE_VERSION = "1.7.0"
FACT_SCHEMA_VERSION = "resident-facts-v1"
DECISION_ENGINE_ID = "resident_interpretation_decision_engine"
DECISION_ENGINE_VERSION = "1.0.0"
SAFETY_POLICY_VERSION = "1.0.0"
REQUIREMENTS_POLICY_VERSION = "1.0.0"
HYBRID_SCORER_ID = "resident_hybrid_interpretation_scorer"
HYBRID_SCORER_VERSION = "1.1.0"


class HybridModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSpan(HybridModel):
    text: EvidenceText


class IssueCategoryEvidence(HybridModel):
    category: IssueCategory
    evidence: EvidenceSpan


class SafetySignal(StrEnum):
    GAS_ODOR = "GAS_ODOR"
    GAS_LEAK = "GAS_LEAK"
    SMOKE = "SMOKE"
    OPEN_FLAME = "OPEN_FLAME"
    ELECTRICAL_ARC = "ELECTRICAL_ARC"
    ELECTRIC_SHOCK = "ELECTRIC_SHOCK"
    ACTIVE_FLOODING = "ACTIVE_FLOODING"
    WATER_NEAR_ELECTRICITY = "WATER_NEAR_ELECTRICITY"
    ELEVATOR_ENTRAPMENT = "ELEVATOR_ENTRAPMENT"
    FALL_HAZARD = "FALL_HAZARD"
    PERSON_INJURED = "PERSON_INJURED"
    PERSON_TRAPPED = "PERSON_TRAPPED"


class SafetyEvidence(HybridModel):
    signal: SafetySignal
    evidence: EvidenceSpan


class CorrectedFact(StrEnum):
    ISSUE_CATEGORY = "ISSUE_CATEGORY"
    ISSUE_LOCATION = "ISSUE_LOCATION"
    ISSUE_DESCRIPTION = "ISSUE_DESCRIPTION"
    AVAILABILITY = "AVAILABILITY"
    OTHER = "OTHER"


class FactConfidence(HybridModel):
    fact: FactName
    confidence: float = Field(ge=0, le=1)


class ExtractedAvailabilityWindow(HybridModel):
    """Provider claim; temporal invariants are enforced by the normalizer."""

    starts_at: datetime
    ends_at: datetime


class ExtractedResidentFactsV1(HybridModel):
    schema_version: Literal["resident-facts-v1"] = "resident-facts-v1"
    issue_description_present: bool
    issue_description_text: str | None = Field(default=None, max_length=4000)
    issue_description_evidence: EvidenceSpan | None = None
    issue_category_evidence: tuple[IssueCategoryEvidence, ...] = Field(default=(), max_length=3)
    location_mentioned: bool
    location_text: str | None = Field(default=None, max_length=255)
    location_evidence: EvidenceSpan | None = None
    existing_ticket_mentioned: bool
    existing_ticket_evidence: EvidenceSpan | None = None
    existing_appointment_mentioned: bool
    existing_appointment_evidence: EvidenceSpan | None = None
    booking_request_mentioned: bool
    booking_request_evidence: EvidenceSpan | None = None
    reschedule_request_mentioned: bool
    reschedule_request_evidence: EvidenceSpan | None = None
    explicit_human_request: bool
    human_request_evidence: EvidenceSpan | None = None
    explicit_cancellation_request: bool
    cancellation_request_evidence: EvidenceSpan | None = None
    status_query_mentioned: bool = False
    status_query_evidence: EvidenceSpan | None = None
    acceptance_decision: AcceptanceDecision | None = None
    acceptance_decision_evidence: EvidenceSpan | None = None
    time_expression_present: bool
    time_expression_text: str | None = Field(default=None, max_length=500)
    time_expression_evidence: EvidenceSpan | None = None
    availability_windows: tuple[ExtractedAvailabilityWindow, ...] = Field(default=(), max_length=20)
    correction_present: bool
    corrected_fields: tuple[CorrectedFact, ...] = Field(default=(), max_length=5)
    safety_evidence: tuple[SafetyEvidence, ...] = Field(default=(), max_length=20)
    unsupported_request_evidence: tuple[EvidenceSpan, ...] = Field(default=(), max_length=10)
    small_talk_only: bool
    confidence_by_fact: tuple[FactConfidence, ...] = Field(default=(), max_length=40)


class NormalizedResidentFactsV1(ExtractedResidentFactsV1):
    source_text: str = Field(min_length=1, max_length=4000)
    rejected_evidence_count: int = Field(default=0, ge=0)
    conflict_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_boolean_evidence_pairs(self) -> NormalizedResidentFactsV1:
        pairs = (
            (self.issue_description_present, self.issue_description_evidence, "description"),
            (self.location_mentioned, self.location_evidence, "location"),
            (self.existing_ticket_mentioned, self.existing_ticket_evidence, "existing ticket"),
            (
                self.existing_appointment_mentioned,
                self.existing_appointment_evidence,
                "existing appointment",
            ),
            (self.booking_request_mentioned, self.booking_request_evidence, "booking"),
            (self.reschedule_request_mentioned, self.reschedule_request_evidence, "reschedule"),
            (self.explicit_human_request, self.human_request_evidence, "human request"),
            (
                self.explicit_cancellation_request,
                self.cancellation_request_evidence,
                "cancellation",
            ),
            (self.status_query_mentioned, self.status_query_evidence, "status query"),
            (
                self.acceptance_decision is not None,
                self.acceptance_decision_evidence,
                "acceptance decision",
            ),
            (self.time_expression_present, self.time_expression_evidence, "time expression"),
        )
        for present, evidence, label in pairs:
            if present != (evidence is not None):
                raise ValueError(f"{label} boolean and evidence must agree")
        if self.issue_description_present != (self.issue_description_text is not None):
            raise ValueError("description boolean and text must agree")
        if self.location_mentioned != (self.location_text is not None):
            raise ValueError("location boolean and text must agree")
        if self.time_expression_present != (self.time_expression_text is not None):
            raise ValueError("time-expression boolean and text must agree")
        if self.correction_present != bool(self.corrected_fields):
            raise ValueError("correction boolean and corrected_fields must agree")
        return self


class DecisionTraceEntry(HybridModel):
    rule_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    outcome: str = Field(min_length=1, max_length=200)


class DecisionTraceV1(HybridModel):
    engine_id: Literal["resident_interpretation_decision_engine"] = (
        "resident_interpretation_decision_engine"
    )
    engine_version: Literal["1.0.0"] = "1.0.0"
    entries: tuple[DecisionTraceEntry, ...] = Field(max_length=30)


class HybridDecision(HybridModel):
    utterance_intent: AgentIntent
    missing_fields: tuple[IssueField, ...]
    clarification_needed: bool
    safety_flags: tuple[SafetyFlag, ...]
    requested_human: bool
    trace: DecisionTraceV1

    @model_validator(mode="after")
    def validate_clarification(self) -> HybridDecision:
        if self.clarification_needed != bool(self.missing_fields):
            raise ValueError("clarification must be derived from missing_fields")
        return self


class ConflictCode(StrEnum):
    HUMAN_INTENT_MISMATCH = "HUMAN_INTENT_MISMATCH"
    RESCHEDULE_INTENT_MISMATCH = "RESCHEDULE_INTENT_MISMATCH"
    SAFETY_MISSING = "SAFETY_MISSING"
    CLARIFICATION_MISMATCH = "CLARIFICATION_MISMATCH"
    RESCHEDULE_WITHOUT_APPOINTMENT = "RESCHEDULE_WITHOUT_APPOINTMENT"
    SMALL_TALK_AS_REPAIR = "SMALL_TALK_AS_REPAIR"


class VerificationVerdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ControlledVerificationResult(HybridModel):
    verdict: VerificationVerdict


class HybridInterpretationMetadata(HybridModel):
    architecture_id: Literal["hybrid_interpretation"] = "hybrid_interpretation"
    architecture_version: str
    architecture_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    fact_prompt_id: Literal["resident_fact_extraction"]
    fact_prompt_version: str
    fact_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_engine_version: str
    safety_policy_version: str
    requirements_policy_version: str
    verification_call_count: int = Field(default=0, ge=0, le=1)
    verification_reason: str | None = Field(default=None, max_length=200)
    verification_result: VerificationVerdict | None = None
    final_decision_source: Literal["DETERMINISTIC", "DETERMINISTIC_AFTER_VERIFICATION"]
