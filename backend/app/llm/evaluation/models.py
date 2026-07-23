"""Strict persisted models for datasets, results, comparisons, and gates."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.agent.enums import IssueField
from app.agent.models import (
    AgentStateSummary,
    ConversationMessage,
    InterpretMessageInput,
    InterpretMessageOutput,
    KnownIssueFields,
)
from app.domain.enums import WorkflowStage

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitCommit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40,64}$")]
Version = Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
CaseId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")]
SafeText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
ScalarValue = str | int | float | bool | None
ExpectedValue = ScalarValue | tuple[str, ...]


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationSuite(StrEnum):
    CREATE_TICKET = "CREATE_TICKET"
    NEED_INFORMATION = "NEED_INFORMATION"
    BOOK_APPOINTMENT = "BOOK_APPOINTMENT"
    RESCHEDULE_APPOINTMENT = "RESCHEDULE_APPOINTMENT"
    REQUEST_HUMAN = "REQUEST_HUMAN"
    SAFETY = "SAFETY"
    SMALL_TALK = "SMALL_TALK"
    UNSUPPORTED = "UNSUPPORTED"
    ADVERSARIAL = "ADVERSARIAL"
    MULTI_TURN_CORRECTION = "MULTI_TURN_CORRECTION"


class EvaluationSeverity(StrEnum):
    STANDARD = "STANDARD"
    CRITICAL = "CRITICAL"


class EvaluationMatcher(StrEnum):
    EXACT = "EXACT"
    ONE_OF = "ONE_OF"
    NULL = "NULL"
    NON_NULL = "NON_NULL"
    EMPTY = "EMPTY"
    NON_EMPTY = "NON_EMPTY"
    SET_EXACT = "SET_EXACT"
    SET_CONTAINS = "SET_CONTAINS"
    SET_EXCLUDES = "SET_EXCLUDES"
    STRING_CONTAINS = "STRING_CONTAINS"
    STRING_EXCLUDES = "STRING_EXCLUDES"
    REGEX = "REGEX"
    NUMBER_RANGE = "NUMBER_RANGE"
    BOOLEAN = "BOOLEAN"


class EvaluationNormalizer(StrEnum):
    NONE = "NONE"
    TRIM = "TRIM"
    CASEFOLD = "CASEFOLD"
    WHITESPACE = "WHITESPACE"
    CHINESE_PUNCTUATION = "CHINESE_PUNCTUATION"
    STRING_SET = "STRING_SET"


class EvaluationCaseStatus(StrEnum):
    PASSED = "PASSED"
    FAILED_EXPECTATION = "FAILED_EXPECTATION"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    SKIPPED = "SKIPPED"
    INCOMPLETE = "INCOMPLETE"


class EvaluationRunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


class EvaluationComparisonStatus(StrEnum):
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"


class CriticalFailureCode(StrEnum):
    CRITICAL_SAFETY_MISSED = "CRITICAL_SAFETY_MISSED"
    REQUEST_HUMAN_BOUNDARY_VIOLATION = "REQUEST_HUMAN_BOUNDARY_VIOLATION"
    PROMPT_LEAKAGE = "PROMPT_LEAKAGE"
    API_KEY_LEAKAGE = "API_KEY_LEAKAGE"
    FORBIDDEN_BUSINESS_ID = "FORBIDDEN_BUSINESS_ID"
    TOOL_CALL_DETECTED = "TOOL_CALL_DETECTED"
    SCHEMA_BYPASS = "SCHEMA_BYPASS"
    MUTATION_BOUNDARY_VIOLATION = "MUTATION_BOUNDARY_VIOLATION"


class EvaluationDatasetMetadata(EvaluationModel):
    dataset_id: Literal["resident_interpretation"]
    dataset_version: Version
    dataset_schema_version: Literal["evaluation-case-v1"]
    description: SafeText
    locale: Literal["zh-CN"]
    case_count: int = Field(ge=1, le=10_000)
    created_for_prompt_id: Literal["resident_interpretation"]
    created_for_schema_version: Literal["interpretation-result-v1"]
    provenance: Literal["synthetic_engineering_fixture"]
    review_status: Literal["engineering_authored"]
    contains_real_personal_data: Literal[False]
    case_file: str = Field(pattern=r"^[a-zA-Z0-9_.-]+\.jsonl$")
    dataset_hash: Sha256


class EvaluationCaseInput(EvaluationModel):
    current_user_message: str = Field(min_length=1, max_length=4000)
    recent_conversation_messages: tuple[ConversationMessage, ...] = Field(default=(), max_length=12)
    current_state_summary: AgentStateSummary = Field(
        default_factory=lambda: AgentStateSummary(intent_version=1)
    )
    current_workflow_stage: WorkflowStage = WorkflowStage.INTAKE
    known_issue_fields: KnownIssueFields = Field(default_factory=KnownIssueFields)
    missing_fields: tuple[IssueField, ...] = Field(default=(), max_length=12)
    reference_time: datetime = Field(
        default_factory=lambda: datetime.fromisoformat("2026-07-23T09:00:00+08:00")
    )
    timezone_name: Literal["Asia/Shanghai"] = "Asia/Shanghai"

    @model_validator(mode="after")
    def validate_formal_input(self) -> EvaluationCaseInput:
        self.to_provider_input()
        return self

    def to_provider_input(self) -> InterpretMessageInput:
        return InterpretMessageInput.model_validate(self.model_dump())


class FieldExpectation(EvaluationModel):
    path: str = Field(pattern=r"^\$\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
    matcher: EvaluationMatcher
    value: ExpectedValue = None
    normalizer: EvaluationNormalizer = EvaluationNormalizer.NONE
    hard: bool = True
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_matcher_value(self) -> FieldExpectation:
        no_value = {
            EvaluationMatcher.NULL,
            EvaluationMatcher.NON_NULL,
            EvaluationMatcher.EMPTY,
            EvaluationMatcher.NON_EMPTY,
        }
        set_matchers = {
            EvaluationMatcher.ONE_OF,
            EvaluationMatcher.SET_EXACT,
            EvaluationMatcher.SET_CONTAINS,
            EvaluationMatcher.SET_EXCLUDES,
        }
        if self.matcher in no_value and self.value is not None:
            raise ValueError(f"{self.matcher.value} does not accept value")
        if self.matcher in set_matchers and (not isinstance(self.value, tuple) or not self.value):
            raise ValueError(f"{self.matcher.value} requires a non-empty string array")
        if self.matcher is EvaluationMatcher.BOOLEAN and not isinstance(self.value, bool):
            raise ValueError("BOOLEAN requires a boolean value")
        if self.matcher is EvaluationMatcher.NUMBER_RANGE:
            if self.minimum is None or self.maximum is None or self.minimum > self.maximum:
                raise ValueError("NUMBER_RANGE requires ordered minimum and maximum")
        elif self.minimum is not None or self.maximum is not None:
            raise ValueError("minimum and maximum are only valid for NUMBER_RANGE")
        if self.matcher is EvaluationMatcher.SET_EXACT and isinstance(self.value, tuple):
            if len(self.value) != len(set(self.value)):
                raise ValueError("SET_EXACT values must be unique")
        return self


class EvaluationExpectation(EvaluationModel):
    fields: tuple[FieldExpectation, ...] = Field(min_length=1)


class ForbiddenExpectation(EvaluationModel):
    business_ids: bool = True
    prompt_leakage: bool = True
    api_key_leakage: bool = True
    tool_call: bool = True
    schema_extra_fields: bool = True


class EvaluationCase(EvaluationModel):
    case_id: CaseId
    case_version: int = Field(ge=1, le=100)
    suite: EvaluationSuite
    severity: EvaluationSeverity
    locale: Literal["zh-CN"]
    tags: tuple[str, ...] = Field(min_length=1, max_length=20)
    description: SafeText
    input: EvaluationCaseInput
    expected: EvaluationExpectation
    forbidden: ForbiddenExpectation = Field(default_factory=ForbiddenExpectation)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not tag.strip() or len(tag) > 80 for tag in value):
            raise ValueError("tags must be non-empty and at most 80 characters")
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value


class EvaluationDataset(EvaluationModel):
    metadata: EvaluationDatasetMetadata
    cases: tuple[EvaluationCase, ...]


class MatcherResult(EvaluationModel):
    path: str
    matcher: EvaluationMatcher
    passed: bool
    hard: bool
    expected: ExpectedValue = None
    actual: ExpectedValue = None
    reason: str = Field(max_length=300)


class EvaluationUsage(EvaluationModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class EvaluationCaseResult(EvaluationModel):
    case_id: CaseId
    suite: EvaluationSuite
    severity: EvaluationSeverity
    repeat_index: int = Field(ge=0, le=4)
    status: EvaluationCaseStatus
    provider: str
    model: str
    prompt_id: str
    prompt_version: str
    prompt_hash: Sha256
    schema_version: str
    input_hash: Sha256
    started_at: datetime
    completed_at: datetime
    latency_ms: int = Field(ge=0)
    attempt_count: int = Field(ge=1, le=5)
    provider_error_code: str | None = None
    interpretation: InterpretMessageOutput | None = None
    matcher_results: tuple[MatcherResult, ...] = ()
    case_passed: bool
    critical_failure_codes: tuple[CriticalFailureCode, ...] = ()
    usage: EvaluationUsage = Field(default_factory=EvaluationUsage)

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_result_time(cls, value: datetime) -> datetime:
        return _require_aware(value)


class EvaluationMetrics(EvaluationModel):
    total_cases: int = Field(ge=0)
    completed_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    provider_failed_cases: int = Field(ge=0)
    invalid_output_cases: int = Field(ge=0)
    skipped_cases: int = Field(ge=0)
    completion_rate: float = Field(ge=0, le=1)
    case_pass_rate: float = Field(ge=0, le=1)
    parse_pass_rate: float = Field(ge=0, le=1)
    schema_pass_rate: float = Field(ge=0, le=1)
    invariant_pass_rate: float = Field(ge=0, le=1)
    intent_accuracy: float = Field(ge=0, le=1)
    requested_action_accuracy: float | None = Field(default=None, ge=0, le=1)
    clarification_accuracy: float = Field(ge=0, le=1)
    missing_fields_precision: float = Field(ge=0, le=1)
    missing_fields_recall: float = Field(ge=0, le=1)
    missing_fields_f1: float = Field(ge=0, le=1)
    safety_signal_precision: float = Field(ge=0, le=1)
    safety_signal_recall: float = Field(ge=0, le=1)
    safety_signal_f1: float = Field(ge=0, le=1)
    critical_safety_recall: float = Field(ge=0, le=1)
    request_human_boundary_accuracy: float = Field(ge=0, le=1)
    prompt_leakage_count: int = Field(ge=0)
    api_key_leakage_count: int = Field(ge=0)
    forbidden_business_id_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    hallucinated_field_count: int = Field(ge=0)
    p50_latency_ms: float | None = Field(default=None, ge=0)
    p95_latency_ms: float | None = Field(default=None, ge=0)
    p99_latency_ms: float | None = Field(default=None, ge=0)
    mean_latency_ms: float | None = Field(default=None, ge=0)
    usage_observed_cases: int = Field(ge=0)
    mean_input_tokens: float | None = Field(default=None, ge=0)
    mean_output_tokens: float | None = Field(default=None, ge=0)
    mean_total_tokens: float | None = Field(default=None, ge=0)
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    exact_output_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    intent_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    requested_action_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    clarification_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    safety_signal_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    authentication_failed_count: int = Field(ge=0)
    permission_denied_count: int = Field(ge=0)
    rate_limited_count: int = Field(ge=0)
    timeout_count: int = Field(ge=0)
    connection_failed_count: int = Field(ge=0)
    upstream_server_error_count: int = Field(ge=0)
    invalid_json_count: int = Field(ge=0)
    schema_validation_failed_count: int = Field(ge=0)
    invariant_violation_count: int = Field(ge=0)
    content_filtered_count: int = Field(ge=0)
    context_length_exceeded_count: int = Field(ge=0)
    unknown_provider_error_count: int = Field(ge=0)
    provider_failure_counts: dict[str, int] = Field(default_factory=dict)


class EvaluationProviderConfiguration(EvaluationModel):
    provider: str
    model: str
    provider_sdk: str
    provider_sdk_version: str | None = None
    prompt_id: str
    prompt_version: str
    prompt_hash: Sha256
    interpretation_schema_version: str
    thinking_mode: str
    temperature: float
    top_p: float
    max_tokens: int = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)
    total_timeout_seconds: float = Field(gt=0)
    max_attempts: int = Field(ge=1, le=5)
    endpoint_fingerprint: Sha256
    live_network: bool = False


class EvaluationRunManifest(EvaluationModel):
    run_id: UUID
    run_schema_version: Literal["evaluation-run-v1"]
    run_status: EvaluationRunStatus
    dataset_id: str
    dataset_version: Version
    dataset_hash: Sha256
    policy_id: str
    policy_version: Version
    policy_hash: Sha256
    scorer_id: Literal["resident_interpretation_scorer"]
    scorer_version: Literal["1.0.0"]
    provider_configuration: EvaluationProviderConfiguration
    concurrency: int = Field(ge=1, le=4)
    repeat_count: int = Field(ge=1, le=5)
    code_commit: GitCommit | None = None
    git_dirty: bool | None = None
    settings_fingerprint: Sha256
    created_at_utc: datetime
    completed_at_utc: datetime | None = None
    case_count: int = Field(ge=1)

    @field_validator("created_at_utc", "completed_at_utc")
    @classmethod
    def validate_manifest_time(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value) if value is not None else None


class MetricRule(EvaluationModel):
    metric: str
    operator: Literal[">=", "==", "<=", "delta>=", "delta<="]
    threshold: float
    severity: Literal["HARD", "WARNING"] = "HARD"


class EvaluationGatePolicy(EvaluationModel):
    policy_id: Literal["resident_interpretation_gate"]
    policy_version: Version
    policy_schema_version: Literal["evaluation-policy-v1"]
    applicable_scorer_id: Literal["resident_interpretation_scorer"]
    absolute_rules: tuple[MetricRule, ...] = Field(min_length=1)
    relative_rules: tuple[MetricRule, ...] = Field(min_length=1)
    policy_hash: Sha256


class GateRuleResult(EvaluationModel):
    metric: str
    operator: str
    threshold: float
    actual: float | int | None
    delta: float | None = None
    severity: str
    message: str


class EvaluationGateResult(EvaluationModel):
    gate_id: UUID
    gate_version: Literal["evaluation-gate-result-v1"]
    run_id: UUID
    scorer_id: Literal["resident_interpretation_scorer"]
    scorer_version: Literal["1.0.0"]
    policy_id: str
    policy_version: Version
    policy_hash: Sha256
    absolute_gate_passed: bool
    relative_gate_evaluated: bool
    relative_gate_passed: bool | None
    overall_passed: bool
    baseline_eligible: bool
    failed_rules: tuple[GateRuleResult, ...]
    warning_rules: tuple[GateRuleResult, ...]
    critical_failures: tuple[CriticalFailureCode, ...]
    evaluated_at_utc: datetime

    @field_validator("evaluated_at_utc")
    @classmethod
    def validate_gate_time(cls, value: datetime) -> datetime:
        return _require_aware(value)


class EvaluationReport(EvaluationModel):
    report_schema_version: Literal["evaluation-report-v1"]
    manifest: EvaluationRunManifest
    metrics: EvaluationMetrics
    gate_result: EvaluationGateResult | None = None


class EvaluationComparison(EvaluationModel):
    comparison_id: UUID
    comparison_schema_version: Literal["evaluation-comparison-v1"]
    status: EvaluationComparisonStatus
    scorer_id: Literal["resident_interpretation_scorer"]
    scorer_version: Literal["1.0.0"]
    baseline_run_id: UUID
    candidate_run_id: UUID
    metric_deltas: dict[str, float] = Field(default_factory=dict)
    failed_rules: tuple[GateRuleResult, ...] = ()
    relative_gate_passed: bool | None = None
    reason: str | None = None


class EvaluationArtifactIndex(EvaluationModel):
    artifact_schema_version: Literal["evaluation-artifact-index-v1"]
    run_id: UUID
    files: tuple[str, ...]
    file_hashes: dict[str, Sha256]


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("evaluation timestamps must be timezone-aware")
    return value
