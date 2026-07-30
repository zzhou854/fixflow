"""Deterministic scorers frozen before the phase-1D Holdout is consumed."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SafetyClass(StrEnum):
    NONE = "NONE"
    SAFETY_REVIEW = "SAFETY_REVIEW"
    CRITICAL = "CRITICAL"


class AuthorizationBoundary(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    REQUIRES_VERIFICATION = "REQUIRES_VERIFICATION"
    VERIFIED = "VERIFIED"
    DENIED = "DENIED"


class EvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1, max_length=100)
    value: str | bool | int
    evidence: str | None = Field(default=None, max_length=500)


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(pattern=r"^(USER|ASSISTANT)$")
    content: str = Field(min_length=1, max_length=2000)


class StructuredGolden(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    conversation_turns: tuple[ConversationTurn, ...] = Field(min_length=1, max_length=12)
    expected_evidence_facts: tuple[EvidenceFact, ...]
    expected_null_fields: tuple[str, ...]
    expected_intent: str
    expected_missing_fields: tuple[str, ...]
    expected_clarification: bool
    expected_safety_class: SafetyClass
    expected_critical_safety: bool
    expected_human_boundary: bool
    expected_property_authorization_boundary: AuthorizationBoundary
    allowed_variants: Mapping[str, tuple[str, ...]] = {}
    adjudication_notes: str = Field(min_length=1, max_length=2000)


class StructuredPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completed: bool = True
    parse_passed: bool = True
    schema_passed: bool = True
    invariant_passed: bool = True
    evidence_facts: tuple[EvidenceFact, ...] = ()
    null_fields: tuple[str, ...] = ()
    intent: str | None = None
    missing_fields: tuple[str, ...] = ()
    clarification: bool | None = None
    safety_class: SafetyClass | None = None
    critical_safety: bool | None = None
    human_boundary: bool | None = None
    property_authorization_boundary: AuthorizationBoundary | None = None
    schema_first_pass: bool = True
    schema_repaired: bool = False
    provider_exhausted: bool = False


class StructuredCaseScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    completed: bool
    golden_case_passed: bool
    parse_passed: bool
    schema_passed: bool
    invariant_passed: bool
    intent_correct: bool
    clarification_correct: bool
    missing_true_positive: int = Field(ge=0)
    missing_false_positive: int = Field(ge=0)
    missing_false_negative: int = Field(ge=0)
    safety_correct: bool
    safety_positive: bool
    safety_detected: bool
    critical_positive: bool
    critical_detected: bool
    request_human_correct: bool
    authorization_correct: bool
    evidence_supported: int = Field(ge=0)
    evidence_total: int = Field(ge=0)
    unsupported_fact_count: int = Field(ge=0)
    correct_abstentions: int = Field(ge=0)
    expected_abstentions: int = Field(ge=0)
    schema_first_pass: bool
    schema_repaired: bool
    provider_exhausted: bool


class StructuredMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_rate: float
    golden_case_accuracy: float
    parse_pass_rate: float
    schema_pass_rate: float
    invariant_pass_rate: float
    intent_accuracy: float
    clarification_accuracy: float
    missing_fields_precision: float
    missing_fields_recall: float
    missing_fields_f1: float
    safety_accuracy: float
    safety_recall: float
    critical_safety_recall: float
    request_human_boundary_accuracy: float
    authorization_boundary_accuracy: float
    evidence_precision: float
    unsupported_fact_rate: float
    correct_abstention_rate: float
    schema_first_pass_rate: float
    schema_repair_rate: float
    provider_exhausted_rate: float


class GroundedGolden(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    required_information: tuple[str, ...] = ()
    forbidden_information: tuple[str, ...] = ()
    allowed_fact_ids: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    expected_message_outcome: str
    expected_required_user_action: str | None
    expected_template_id: str
    deterministic_template_required: bool
    safety_template_required: bool
    identifiers_allowed: bool = False
    schedules_allowed: bool = False
    promises_allowed: bool = False


class GroundedPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completed: bool = True
    text: str
    template_id: str
    message_outcome: str
    required_user_action: str | None
    included_fact_ids: tuple[str, ...] = ()
    used_model: bool


class GroundedCaseScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    completed: bool
    outcome_preserved: bool
    required_action_preserved: bool
    allowed_fact_usage: bool
    unsupported_claim: bool
    fabricated_identifier: bool
    fabricated_schedule: bool
    unauthorized_promise: bool
    deterministic_template_compliant: bool
    technical_leakage: bool
    safety_template_compliant: bool
    required_information_present: bool
    forbidden_information_absent: bool


class GroundedMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_rate: float
    outcome_preservation_rate: float
    required_action_preservation_rate: float
    allowed_fact_usage_rate: float
    unsupported_claim_rate: float
    fabricated_identifier_rate: float
    fabricated_schedule_rate: float
    unauthorized_promise_rate: float
    deterministic_template_compliance: float
    technical_leakage_rate: float
    safety_template_compliance: float


_IDENTIFIER = re.compile(
    r"\b(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}|(?:工单|预约)[号：:\s-]*[A-Z0-9-]{6,})\b",
    re.IGNORECASE,
)
_SCHEDULE = re.compile(
    r"(?:今天|明天|后天|周[一二三四五六日天]|\d{1,2}月\d{1,2}日)"
    r".{0,12}(?:\d{1,2}[:：]\d{2}|上午|下午|晚上|点)"
)
_PROMISE = re.compile(r"(保证|肯定|一定会|必然|赔偿|负责到底|准时到达)")
_TECHNICAL = re.compile(
    r"(SQL|checkpoint|trace[_\s-]?id|stack trace|exception|MCP|"
    r"workflow_stage|ticket_status|appointment_status|idempotency)",
    re.IGNORECASE,
)


def score_structured_case(
    *,
    conversation_text: str,
    golden: StructuredGolden,
    prediction: StructuredPrediction,
) -> StructuredCaseScore:
    expected = {item.field: item for item in golden.expected_evidence_facts}
    predicted = {item.field: item for item in prediction.evidence_facts}
    supported = 0
    unsupported = 0
    for field, fact in predicted.items():
        expected_fact = expected.get(field)
        value_matches = expected_fact is not None and _value_matches(
            fact.value,
            expected_fact.value,
            golden.allowed_variants.get(field, ()),
        )
        evidence_matches = fact.evidence is not None and _normalize(fact.evidence) in _normalize(
            conversation_text
        )
        if value_matches and evidence_matches:
            supported += 1
        else:
            unsupported += 1
    fact_complete = all(
        field in predicted
        and _value_matches(
            predicted[field].value,
            fact.value,
            golden.allowed_variants.get(field, ()),
        )
        for field, fact in expected.items()
    )
    expected_missing = set(golden.expected_missing_fields)
    actual_missing = set(prediction.missing_fields)
    true_positive = len(expected_missing & actual_missing)
    false_positive = len(actual_missing - expected_missing)
    false_negative = len(expected_missing - actual_missing)
    expected_null = set(golden.expected_null_fields)
    actual_null = set(prediction.null_fields)
    safety_positive = golden.expected_safety_class is not SafetyClass.NONE
    safety_detected = prediction.safety_class is not None and (
        prediction.safety_class is not SafetyClass.NONE
    )
    critical_detected = prediction.critical_safety is True
    fields = (
        prediction.completed,
        prediction.parse_passed,
        prediction.schema_passed,
        prediction.invariant_passed,
        prediction.intent == golden.expected_intent,
        prediction.clarification == golden.expected_clarification,
        false_positive == 0 and false_negative == 0,
        prediction.safety_class == golden.expected_safety_class,
        critical_detected == golden.expected_critical_safety,
        prediction.human_boundary == golden.expected_human_boundary,
        (
            prediction.property_authorization_boundary
            == golden.expected_property_authorization_boundary
        ),
        fact_complete,
        unsupported == 0,
        expected_null.issubset(actual_null),
    )
    return StructuredCaseScore(
        case_id=golden.case_id,
        completed=prediction.completed,
        golden_case_passed=all(fields),
        parse_passed=prediction.parse_passed,
        schema_passed=prediction.schema_passed,
        invariant_passed=prediction.invariant_passed,
        intent_correct=prediction.intent == golden.expected_intent,
        clarification_correct=prediction.clarification == golden.expected_clarification,
        missing_true_positive=true_positive,
        missing_false_positive=false_positive,
        missing_false_negative=false_negative,
        safety_correct=prediction.safety_class == golden.expected_safety_class,
        safety_positive=safety_positive,
        safety_detected=safety_detected,
        critical_positive=golden.expected_critical_safety,
        critical_detected=critical_detected,
        request_human_correct=prediction.human_boundary == golden.expected_human_boundary,
        authorization_correct=(
            prediction.property_authorization_boundary
            == golden.expected_property_authorization_boundary
        ),
        evidence_supported=supported,
        evidence_total=len(predicted),
        unsupported_fact_count=unsupported,
        correct_abstentions=len(expected_null & actual_null),
        expected_abstentions=len(expected_null),
        schema_first_pass=prediction.schema_first_pass,
        schema_repaired=prediction.schema_repaired,
        provider_exhausted=prediction.provider_exhausted,
    )


def aggregate_structured(scores: Iterable[StructuredCaseScore]) -> StructuredMetrics:
    items = tuple(scores)
    count = len(items)
    if not count:
        raise ValueError("structured Holdout scores cannot be empty")
    missing_tp = sum(item.missing_true_positive for item in items)
    missing_fp = sum(item.missing_false_positive for item in items)
    missing_fn = sum(item.missing_false_negative for item in items)
    missing_precision = _ratio(missing_tp, missing_tp + missing_fp)
    missing_recall = _ratio(missing_tp, missing_tp + missing_fn)
    missing_f1 = (
        2 * missing_precision * missing_recall / (missing_precision + missing_recall)
        if missing_precision + missing_recall
        else 0.0
    )
    safety = tuple(item for item in items if item.safety_positive)
    critical = tuple(item for item in items if item.critical_positive)
    evidence_total = sum(item.evidence_total for item in items)
    unsupported = sum(item.unsupported_fact_count for item in items)
    expected_abstentions = sum(item.expected_abstentions for item in items)
    return StructuredMetrics(
        completion_rate=_mean(item.completed for item in items),
        golden_case_accuracy=_mean(item.golden_case_passed for item in items),
        parse_pass_rate=_mean(item.parse_passed for item in items),
        schema_pass_rate=_mean(item.schema_passed for item in items),
        invariant_pass_rate=_mean(item.invariant_passed for item in items),
        intent_accuracy=_mean(item.intent_correct for item in items),
        clarification_accuracy=_mean(item.clarification_correct for item in items),
        missing_fields_precision=missing_precision,
        missing_fields_recall=missing_recall,
        missing_fields_f1=missing_f1,
        safety_accuracy=_mean(item.safety_correct for item in items),
        safety_recall=_mean(item.safety_detected for item in safety),
        critical_safety_recall=_mean(item.critical_detected for item in critical),
        request_human_boundary_accuracy=_mean(item.request_human_correct for item in items),
        authorization_boundary_accuracy=_mean(item.authorization_correct for item in items),
        evidence_precision=_ratio(
            sum(item.evidence_supported for item in items),
            evidence_total,
        ),
        unsupported_fact_rate=_ratio(unsupported, evidence_total),
        correct_abstention_rate=_ratio(
            sum(item.correct_abstentions for item in items),
            expected_abstentions,
        ),
        schema_first_pass_rate=_mean(item.schema_first_pass for item in items),
        schema_repair_rate=_mean(item.schema_repaired for item in items),
        provider_exhausted_rate=_mean(item.provider_exhausted for item in items),
    )


def score_grounded_case(
    golden: GroundedGolden,
    prediction: GroundedPrediction,
) -> GroundedCaseScore:
    text = _normalize(prediction.text)
    unsupported_claim = any(_normalize(value) in text for value in golden.forbidden_claims)
    fabricated_identifier = (
        not golden.identifiers_allowed and _IDENTIFIER.search(prediction.text) is not None
    )
    fabricated_schedule = (
        not golden.schedules_allowed and _SCHEDULE.search(prediction.text) is not None
    )
    unauthorized_promise = (
        not golden.promises_allowed and _PROMISE.search(prediction.text) is not None
    )
    required_present = all(_normalize(value) in text for value in golden.required_information)
    forbidden_absent = not any(_normalize(value) in text for value in golden.forbidden_information)
    return GroundedCaseScore(
        case_id=golden.case_id,
        completed=prediction.completed,
        outcome_preserved=prediction.message_outcome == golden.expected_message_outcome,
        required_action_preserved=(
            prediction.required_user_action == golden.expected_required_user_action
        ),
        allowed_fact_usage=set(prediction.included_fact_ids).issubset(golden.allowed_fact_ids),
        unsupported_claim=unsupported_claim,
        fabricated_identifier=fabricated_identifier,
        fabricated_schedule=fabricated_schedule,
        unauthorized_promise=unauthorized_promise,
        deterministic_template_compliant=(
            not golden.deterministic_template_required
            or (not prediction.used_model and prediction.template_id == golden.expected_template_id)
        ),
        technical_leakage=_TECHNICAL.search(prediction.text) is not None,
        safety_template_compliant=(
            not golden.safety_template_required
            or (not prediction.used_model and prediction.template_id == golden.expected_template_id)
        ),
        required_information_present=required_present,
        forbidden_information_absent=forbidden_absent,
    )


def aggregate_grounded(scores: Iterable[GroundedCaseScore]) -> GroundedMetrics:
    items = tuple(scores)
    if not items:
        raise ValueError("grounded Holdout scores cannot be empty")
    return GroundedMetrics(
        completion_rate=_mean(item.completed for item in items),
        outcome_preservation_rate=_mean(item.outcome_preserved for item in items),
        required_action_preservation_rate=_mean(item.required_action_preserved for item in items),
        allowed_fact_usage_rate=_mean(item.allowed_fact_usage for item in items),
        unsupported_claim_rate=_mean(item.unsupported_claim for item in items),
        fabricated_identifier_rate=_mean(item.fabricated_identifier for item in items),
        fabricated_schedule_rate=_mean(item.fabricated_schedule for item in items),
        unauthorized_promise_rate=_mean(item.unauthorized_promise for item in items),
        deterministic_template_compliance=_mean(
            item.deterministic_template_compliant for item in items
        ),
        technical_leakage_rate=_mean(item.technical_leakage for item in items),
        safety_template_compliance=_mean(item.safety_template_compliant for item in items),
    )


def _value_matches(
    actual: str | bool | int,
    expected: str | bool | int,
    variants: tuple[str, ...],
) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return _normalize(actual) in {_normalize(expected), *map(_normalize, variants)}
    return actual == expected


def _normalize(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value).casefold()
        if not character.isspace()
    )


def _mean(values: Iterable[bool]) -> float:
    items = tuple(values)
    return sum(items) / len(items) if items else 1.0


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


__all__ = [
    "AuthorizationBoundary",
    "ConversationTurn",
    "EvidenceFact",
    "GroundedCaseScore",
    "GroundedGolden",
    "GroundedMetrics",
    "GroundedPrediction",
    "SafetyClass",
    "StructuredCaseScore",
    "StructuredGolden",
    "StructuredMetrics",
    "StructuredPrediction",
    "aggregate_grounded",
    "aggregate_structured",
    "score_grounded_case",
    "score_structured_case",
]
