"""Deterministic scorers frozen before the phase-1D Holdout is consumed."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class SafetyClass(StrEnum):
    NONE = "NONE"
    SAFETY_REVIEW = "SAFETY_REVIEW"
    CRITICAL = "CRITICAL"


class AuthorizationBoundary(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    REQUIRES_VERIFICATION = "REQUIRES_VERIFICATION"
    VERIFIED = "VERIFIED"
    DENIED = "DENIED"


class EvidenceSource(StrEnum):
    USER_TURN = "USER_TURN"
    KNOWN_ISSUE_FIELD = "KNOWN_ISSUE_FIELD"


class EvidenceFact(BaseModel):
    """A field-level evidence binding.

    The validation aliases keep the frozen 1.0.0 assets readable. Revised
    2.1.0 Golden files serialize the explicit 1.1.0 field names.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    field_name: str = Field(
        validation_alias=AliasChoices("field_name", "field"),
        min_length=1,
        max_length=100,
    )
    normalized_value: str | bool | int = Field(
        validation_alias=AliasChoices("normalized_value", "value")
    )
    evidence_turn_id: str | None = Field(default=None, min_length=1, max_length=100)
    evidence_span: str | None = Field(
        default=None,
        validation_alias=AliasChoices("evidence_span", "evidence"),
        max_length=500,
    )
    evidence_start: int | None = Field(default=None, ge=0)
    evidence_end: int | None = Field(default=None, ge=0)
    evidence_source: EvidenceSource = EvidenceSource.USER_TURN
    inference_allowed: bool = False

    @model_validator(mode="after")
    def validate_coordinates(self) -> EvidenceFact:
        positions = (self.evidence_start, self.evidence_end)
        if (positions[0] is None) != (positions[1] is None):
            raise ValueError("evidence_start and evidence_end must be provided together")
        if (
            self.evidence_start is not None
            and self.evidence_end is not None
            and self.evidence_end <= self.evidence_start
        ):
            raise ValueError("evidence_end must be greater than evidence_start")
        return self

    # Compatibility properties for the immutable 1.0.0 scorer.
    @property
    def field(self) -> str:
        return self.field_name

    @property
    def value(self) -> str | bool | int:
        return self.normalized_value

    @property
    def evidence(self) -> str | None:
        return self.evidence_span


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_id: str | None = Field(default=None, min_length=1, max_length=100)
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
    known_issue_fields: Mapping[str, str | bool | int] = {}
    current_user_turn_id: str | None = None
    adjudication_notes: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_revised_turn_contract(self) -> StructuredGolden:
        if self.current_user_turn_id is None:
            return self
        turn_ids = tuple(turn.turn_id for turn in self.conversation_turns)
        if any(turn_id is None for turn_id in turn_ids):
            raise ValueError("revised Golden requires an ID on every conversation turn")
        if len(set(turn_ids)) != len(turn_ids):
            raise ValueError("conversation turn IDs must be unique")
        current = next(
            (turn for turn in self.conversation_turns if turn.turn_id == self.current_user_turn_id),
            None,
        )
        if current is None or current.role != "USER":
            raise ValueError("current_user_turn_id must identify a USER turn")
        return self


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
    evidence_expected: int = Field(default=0, ge=0)
    unsupported_fact_count: int = Field(ge=0)
    mismatched_evidence_count: int = Field(default=0, ge=0)
    stale_evidence_count: int = Field(default=0, ge=0)
    negation_evidence_error_count: int = Field(default=0, ge=0)
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
    field_level_evidence_precision: float
    field_level_evidence_recall: float
    unsupported_fact_rate: float
    unsupported_field_rate: float
    mismatched_evidence_rate: float
    stale_evidence_rate: float
    negation_evidence_error_rate: float
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
    grounded_generation_allowed: bool = False
    safety_template_required: bool
    expected_business_status: str | None = None
    expected_display_action_text: str | None = None
    identifiers_allowed: bool = False
    schedules_allowed: bool = False
    promises_allowed: bool = False
    verified_display_identifiers: tuple[str, ...] = ()
    verified_appointment_windows: tuple[str, ...] = ()
    candidate_appointment_windows: tuple[str, ...] = ()


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
    required_information_found: int = Field(default=0, ge=0)
    required_information_total: int = Field(default=0, ge=0)
    forbidden_information_violations: int = Field(default=0, ge=0)
    forbidden_information_total: int = Field(default=0, ge=0)
    template_mapping_correct: bool = True
    status_semantics_preserved: bool = True
    allowed_fact_supported: int = Field(default=0, ge=0)
    allowed_fact_total: int = Field(default=0, ge=0)
    natural_response_completed: bool = True
    semantic_template_compliant: bool = True


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
    required_information_coverage: float
    forbidden_information_violation_rate: float
    template_mapping_accuracy: float
    status_semantics_preservation_rate: float
    allowed_fact_precision: float
    natural_response_completion_rate: float
    semantic_template_compliance: float


_IDENTIFIER = re.compile(
    r"\b(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}|(?:工单|预约)[号：:\s-]*[A-Z0-9-]{6,})\b",
    re.IGNORECASE,
)
_SCHEDULE = re.compile(
    r"(?:(?:今天|明天|后天|周[一二三四五六日天]|\d{1,2}月\d{1,2}日)"
    r".{0,12}(?:\d{1,2}[:：]\d{2}|上午|下午|晚上|点)|"
    r"(?:预约|上门|师傅).{0,12}(?:今天|明天|后天|周[一二三四五六日天]|"
    r"\d{1,2}月\d{1,2}日))"
)
_PROMISE = re.compile(r"(保证|肯定|一定会|必然|赔偿|负责到底|准时到达)")
_TECHNICAL_WORDS = re.compile(
    r"(?<![a-z0-9])(?:"
    r"model[\s_-]*provider|provider|deep[\s_-]*seek(?:[\s_-]*v?4)?"
    r"(?:[\s_-]*(?:flash|pro))?|flash|pro|json[\s_-]*schema|schema|"
    r"system[\s_-]*prompt|prompt|uuid|server[\s_-]*sent[\s_-]*events?|sse|"
    r"trace|replay|checkpoint|lang[\s_-]*graph|lang[\s_-]*chain|mcp|"
    r"idempotency[\s_-]*key|run[\s_-]*id|thread[\s_-]*id|event[\s_-]*id|"
    r"intent[\s_-]*version|unknown[\s_-]*commit|human[\s_-]*review|"
    r"awaiting[\s_-]*slot[\s_-]*confirmation|booking[\s_-]*guaranteed|"
    r"policy[\s_-]*evidence[\s_-]*id|workflow[\s_-]*stage|"
    r"ticket[\s_-]*status|appointment[\s_-]*status|stack[\s_-]*trace|"
    r"exception|sql"
    r")(?![a-z0-9])",
    re.IGNORECASE,
)
_TECHNICAL_CHINESE = re.compile(
    r"(模型供应商|模型提供商|系统提示词|结构化模式|幂等键|"
    r"服务端推送事件|检查点|回放运行|策略证据编号)"
)
_TECHNICAL_COMPACT = (
    "modelprovider",
    "deepseek",
    "jsonschema",
    "systemprompt",
    "serversentevent",
    "checkpoint",
    "langgraph",
    "langchain",
    "idempotencykey",
    "runid",
    "threadid",
    "eventid",
    "intentversion",
    "unknowncommit",
    "humanreview",
    "awaitingslotconfirmation",
    "bookingguaranteed",
    "policyevidenceid",
)
_NEGATION = re.compile(r"(?:不是|并非|没有|没在|不在|不要|别把|取消|作废)\s*$")
_CORRECTION = re.compile(r"(?:不是.+(?:而是|是)|刚才说错|改成|更正为|应当是|其实是)")


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
        evidence_expected=len(expected),
        unsupported_fact_count=unsupported,
        correct_abstentions=len(expected_null & actual_null),
        expected_abstentions=len(expected_null),
        schema_first_pass=prediction.schema_first_pass,
        schema_repaired=prediction.schema_repaired,
        provider_exhausted=prediction.provider_exhausted,
    )


def score_structured_case_v1_1(
    *,
    golden: StructuredGolden,
    prediction: StructuredPrediction,
) -> StructuredCaseScore:
    """Score revised Holdout evidence against exact, field-owned bindings."""

    turns = {
        turn.turn_id: (index, turn)
        for index, turn in enumerate(golden.conversation_turns)
        if turn.turn_id is not None
    }
    expected_by_field = {item.field_name: item for item in golden.expected_evidence_facts}
    supported_fields: set[str] = set()
    unsupported = 0
    mismatched = 0
    stale = 0
    negation_errors = 0

    for fact in prediction.evidence_facts:
        expected = expected_by_field.get(fact.field_name)
        value_matches = expected is not None and _value_matches(
            fact.normalized_value,
            expected.normalized_value,
            golden.allowed_variants.get(fact.field_name, ()),
        )
        binding_valid = _binding_is_valid(
            fact,
            expected=expected,
            turns=turns,
            known_issue_fields=golden.known_issue_fields,
        )
        if value_matches and binding_valid:
            supported_fields.add(fact.field_name)
            continue

        unsupported += 1
        if expected is not None and value_matches:
            mismatched += 1
        if _is_stale_evidence(fact, expected=expected, turns=turns):
            stale += 1
        if _is_negated_evidence(fact, turns=turns):
            negation_errors += 1

    expected_missing = set(golden.expected_missing_fields)
    actual_missing = set(prediction.missing_fields)
    true_positive = len(expected_missing & actual_missing)
    false_positive = len(actual_missing - expected_missing)
    false_negative = len(expected_missing - actual_missing)
    expected_null = set(golden.expected_null_fields)
    actual_null = set(prediction.null_fields)
    safety_positive = golden.expected_safety_class is not SafetyClass.NONE
    safety_detected = prediction.safety_class not in (None, SafetyClass.NONE)
    critical_detected = prediction.critical_safety is True
    fact_complete = supported_fields == set(expected_by_field)
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
        evidence_supported=len(supported_fields),
        evidence_total=len(prediction.evidence_facts),
        evidence_expected=len(expected_by_field),
        unsupported_fact_count=unsupported,
        mismatched_evidence_count=mismatched,
        stale_evidence_count=stale,
        negation_evidence_error_count=negation_errors,
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
    evidence_expected = sum(item.evidence_expected for item in items)
    unsupported = sum(item.unsupported_fact_count for item in items)
    mismatched = sum(item.mismatched_evidence_count for item in items)
    stale = sum(item.stale_evidence_count for item in items)
    negation_errors = sum(item.negation_evidence_error_count for item in items)
    expected_abstentions = sum(item.expected_abstentions for item in items)
    evidence_precision = _ratio(
        sum(item.evidence_supported for item in items),
        evidence_total,
    )
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
        evidence_precision=evidence_precision,
        field_level_evidence_precision=evidence_precision,
        field_level_evidence_recall=_ratio(
            sum(item.evidence_supported for item in items),
            evidence_expected,
        ),
        unsupported_fact_rate=_ratio(unsupported, evidence_total),
        unsupported_field_rate=_ratio(unsupported, evidence_total),
        mismatched_evidence_rate=_ratio(mismatched, evidence_total),
        stale_evidence_rate=_ratio(stale, evidence_total),
        negation_evidence_error_rate=_ratio(negation_errors, evidence_total),
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
    fabricated_identifier = _contains_unverified_match(
        _IDENTIFIER,
        prediction.text,
        golden.verified_display_identifiers if golden.identifiers_allowed else (),
    )
    fabricated_schedule = _contains_unverified_match(
        _SCHEDULE,
        prediction.text,
        golden.verified_appointment_windows if golden.schedules_allowed else (),
    )
    unauthorized_promise = (
        not golden.promises_allowed and _PROMISE.search(prediction.text) is not None
    )
    required_found = sum(_normalize(value) in text for value in golden.required_information)
    forbidden_violations = sum(_normalize(value) in text for value in golden.forbidden_information)
    required_present = required_found == len(golden.required_information)
    forbidden_absent = forbidden_violations == 0
    template_mapping_correct = prediction.template_id == golden.expected_template_id
    status_semantics_preserved = (
        template_mapping_correct and forbidden_absent and not unsupported_claim
    )
    allowed_fact_ids = set(golden.allowed_fact_ids)
    included_fact_ids = tuple(dict.fromkeys(prediction.included_fact_ids))
    allowed_fact_supported = sum(item in allowed_fact_ids for item in included_fact_ids)
    outcome_preserved = prediction.message_outcome == golden.expected_message_outcome
    action_preserved = prediction.required_user_action == golden.expected_required_user_action
    semantic_template_compliant = (
        outcome_preserved
        and action_preserved
        and template_mapping_correct
        and required_present
        and forbidden_absent
        and not unsupported_claim
    )
    return GroundedCaseScore(
        case_id=golden.case_id,
        completed=prediction.completed,
        outcome_preserved=outcome_preserved,
        required_action_preserved=action_preserved,
        allowed_fact_usage=set(included_fact_ids).issubset(allowed_fact_ids),
        unsupported_claim=unsupported_claim,
        fabricated_identifier=fabricated_identifier,
        fabricated_schedule=fabricated_schedule,
        unauthorized_promise=unauthorized_promise,
        deterministic_template_compliant=(
            not golden.deterministic_template_required
            or (not prediction.used_model and prediction.template_id == golden.expected_template_id)
        ),
        technical_leakage=_has_technical_leakage(prediction.text),
        safety_template_compliant=(
            not golden.safety_template_required
            or (not prediction.used_model and prediction.template_id == golden.expected_template_id)
        ),
        required_information_present=required_present,
        forbidden_information_absent=forbidden_absent,
        required_information_found=required_found,
        required_information_total=len(golden.required_information),
        forbidden_information_violations=forbidden_violations,
        forbidden_information_total=len(golden.forbidden_information),
        template_mapping_correct=template_mapping_correct,
        status_semantics_preserved=status_semantics_preserved,
        allowed_fact_supported=allowed_fact_supported,
        allowed_fact_total=len(included_fact_ids),
        natural_response_completed=(
            prediction.completed if golden.grounded_generation_allowed else True
        ),
        semantic_template_compliant=semantic_template_compliant,
    )


def aggregate_grounded(scores: Iterable[GroundedCaseScore]) -> GroundedMetrics:
    items = tuple(scores)
    if not items:
        raise ValueError("grounded Holdout scores cannot be empty")
    required_total = sum(item.required_information_total for item in items)
    required_found = sum(item.required_information_found for item in items)
    forbidden_violations = sum(item.forbidden_information_violations for item in items)
    forbidden_total = sum(item.forbidden_information_total for item in items)
    allowed_fact_supported = sum(item.allowed_fact_supported for item in items)
    allowed_fact_total = sum(item.allowed_fact_total for item in items)
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
        required_information_coverage=_ratio(required_found, required_total),
        forbidden_information_violation_rate=_ratio(
            forbidden_violations,
            forbidden_total,
        ),
        template_mapping_accuracy=_mean(item.template_mapping_correct for item in items),
        status_semantics_preservation_rate=_mean(item.status_semantics_preserved for item in items),
        allowed_fact_precision=_ratio(allowed_fact_supported, allowed_fact_total),
        natural_response_completion_rate=_mean(item.natural_response_completed for item in items),
        semantic_template_compliance=_mean(item.semantic_template_compliant for item in items),
    )


def _binding_is_valid(
    fact: EvidenceFact,
    *,
    expected: EvidenceFact | None,
    turns: Mapping[str, tuple[int, ConversationTurn]],
    known_issue_fields: Mapping[str, str | bool | int],
) -> bool:
    if expected is None or fact.inference_allowed:
        return False
    if fact.evidence_source is EvidenceSource.KNOWN_ISSUE_FIELD:
        known = known_issue_fields.get(fact.field_name)
        return (
            expected.evidence_source is EvidenceSource.KNOWN_ISSUE_FIELD
            and known is not None
            and _value_matches(fact.normalized_value, known, ())
            and fact.evidence_turn_id == expected.evidence_turn_id
            and fact.evidence_span == expected.evidence_span
            and fact.evidence_start == expected.evidence_start
            and fact.evidence_end == expected.evidence_end
        )
    if (
        fact.evidence_turn_id is None
        or fact.evidence_span is None
        or fact.evidence_start is None
        or fact.evidence_end is None
    ):
        return False
    located = turns.get(fact.evidence_turn_id)
    if located is None:
        return False
    _, turn = located
    if turn.role != "USER" or fact.evidence_end > len(turn.content):
        return False
    if turn.content[fact.evidence_start : fact.evidence_end] != fact.evidence_span:
        return False
    return (
        expected.evidence_source is EvidenceSource.USER_TURN
        and fact.evidence_turn_id == expected.evidence_turn_id
        and fact.evidence_span == expected.evidence_span
        and fact.evidence_start == expected.evidence_start
        and fact.evidence_end == expected.evidence_end
        and not _is_negated_evidence(fact, turns=turns)
    )


def _is_negated_evidence(
    fact: EvidenceFact,
    *,
    turns: Mapping[str, tuple[int, ConversationTurn]],
) -> bool:
    if fact.evidence_turn_id is None:
        return False
    located = turns.get(fact.evidence_turn_id)
    if located is None or fact.evidence_start is None or fact.evidence_end is None:
        return False
    _, turn = located
    start = max(0, fact.evidence_start - 12)
    return _NEGATION.search(turn.content[start : fact.evidence_start]) is not None


def _is_stale_evidence(
    fact: EvidenceFact,
    *,
    expected: EvidenceFact | None,
    turns: Mapping[str, tuple[int, ConversationTurn]],
) -> bool:
    if expected is None:
        return False
    if fact.evidence_turn_id is None or expected.evidence_turn_id is None:
        return False
    actual_turn = turns.get(fact.evidence_turn_id)
    expected_turn = turns.get(expected.evidence_turn_id)
    if actual_turn is None or expected_turn is None:
        return False
    actual_index, _ = actual_turn
    expected_index, expected_value = expected_turn
    return actual_index < expected_index and _CORRECTION.search(expected_value.content) is not None


def _has_technical_leakage(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    compact = re.sub(r"[\W_]+", "", normalized.casefold(), flags=re.UNICODE)
    return (
        _TECHNICAL_WORDS.search(normalized) is not None
        or _TECHNICAL_CHINESE.search(normalized) is not None
        or any(term in compact for term in _TECHNICAL_COMPACT)
    )


def _contains_unverified_match(
    pattern: re.Pattern[str],
    text: str,
    verified_values: tuple[str, ...],
) -> bool:
    remaining = unicodedata.normalize("NFKC", text)
    for value in verified_values:
        remaining = re.sub(
            re.escape(unicodedata.normalize("NFKC", value)),
            "",
            remaining,
            flags=re.IGNORECASE,
        )
    return pattern.search(remaining) is not None


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
    "EvidenceSource",
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
    "score_structured_case_v1_1",
]
