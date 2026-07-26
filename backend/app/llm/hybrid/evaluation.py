"""Safe, sequential development evaluation for the hybrid architecture."""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from app.agent.errors import StructuredOutputInvalid
from app.llm.errors import LLMProviderError
from app.llm.evaluation.artifacts import atomic_write_text
from app.llm.evaluation.development import (
    DevelopmentGateResult,
    evaluate_development_gate,
    evaluate_smoke_gate,
)
from app.llm.evaluation.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationMetrics,
    EvaluationModel,
)
from app.llm.evaluation.scorer_v2 import score_failure
from app.llm.hybrid.models import HybridInterpretationMetadata, NormalizedResidentFactsV1
from app.llm.hybrid.pipeline import HybridInterpretationNode
from app.llm.hybrid.scorer import calculate_hybrid_metrics, score_hybrid_success


class HybridFactMetrics(EvaluationModel):
    fact_precision: float = Field(ge=0, le=1)
    fact_recall: float = Field(ge=0, le=1)
    fact_f1: float = Field(ge=0, le=1)
    evidence_span_validity: float = Field(ge=0, le=1)
    explicit_human_request_recall: float = Field(ge=0, le=1)
    existing_ticket_fact_accuracy: float = Field(ge=0, le=1)
    existing_appointment_fact_accuracy: float = Field(ge=0, le=1)
    time_expression_presence_accuracy: float = Field(ge=0, le=1)
    correction_detection_accuracy: float = Field(ge=0, le=1)
    safety_evidence_recall: float = Field(ge=0, le=1)
    unsupported_fact_hallucination_count: int = Field(ge=0)


class HybridSafeCaseSummary(EvaluationModel):
    case_id: str
    repeat_index: int
    suite: str
    severity: str
    status: str
    case_passed: bool
    failure_paths: tuple[str, ...]
    failure_details: tuple[str, ...]
    critical_failure_codes: tuple[str, ...]
    rejected_evidence_count: int
    evidence_count: int
    verification_call_count: int
    latency_ms: int
    attempt_count: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    utterance_intent: str | None = None
    missing_fields: tuple[str, ...] = ()
    safety_flags: tuple[str, ...] = ()
    requested_human: bool | None = None


class HybridEvaluationReport(EvaluationModel):
    report_schema_version: Literal["hybrid-evaluation-report-v1"] = "hybrid-evaluation-report-v1"
    run_id: UUID
    stage: Literal[
        "PROBE",
        "SMOKE",
        "DEVELOPMENT",
        "HISTORICAL_REGRESSION",
        "FORMAL_REGRESSION",
        "FORMAL_CHALLENGE",
    ]
    development_only: bool
    challenge_consumed: bool = False
    dataset_id: str
    dataset_version: str
    dataset_hash: str
    provider: str
    model: str
    runtime_commit: str | None = None
    source_clean: bool = False
    architecture: HybridInterpretationMetadata
    started_at: datetime
    completed_at: datetime
    evaluation_call_count: int
    upstream_attempt_count: int
    verification_call_count: int
    metrics: EvaluationMetrics
    fact_metrics: HybridFactMetrics
    gate: DevelopmentGateResult
    provider_failure_counts: dict[str, int]
    cases: tuple[HybridSafeCaseSummary, ...]


class HybridStabilityReport(EvaluationModel):
    stability_schema_version: Literal["hybrid-stability-v1"] = "hybrid-stability-v1"
    first_run_id: UUID
    second_run_id: UUID
    intent_consistency_rate: float = Field(ge=0, le=1)
    clarification_consistency_rate: float = Field(ge=0, le=1)
    missing_fields_consistency_rate: float = Field(ge=0, le=1)
    safety_consistency_rate: float = Field(ge=0, le=1)
    critical_safety_consistency_rate: float = Field(ge=0, le=1)
    request_human_consistency_rate: float = Field(ge=0, le=1)
    passed: bool
    failed_rules: tuple[str, ...] = ()


type HybridStage = Literal[
    "PROBE",
    "SMOKE",
    "DEVELOPMENT",
    "HISTORICAL_REGRESSION",
    "FORMAL_REGRESSION",
    "FORMAL_CHALLENGE",
]


async def run_hybrid_evaluation(
    *,
    dataset: EvaluationDataset,
    node: HybridInterpretationNode,
    provider: str,
    model: str,
    stage: HybridStage,
    repeat_count: int,
    output_path: Path,
    minimum_interval_seconds: float = 0,
    runtime_commit: str | None = None,
    source_clean: bool = False,
    progress: Callable[[int, int, EvaluationCaseResult], None] | None = None,
) -> HybridEvaluationReport:
    if repeat_count not in {1, 2}:
        raise ValueError("hybrid evaluation repeat_count must be 1 or 2")
    started_at = datetime.now(UTC)
    results: list[EvaluationCaseResult] = []
    safe_cases: list[HybridSafeCaseSummary] = []
    fact_observations: list[tuple[EvaluationCase, NormalizedResidentFactsV1]] = []
    last_started = 0.0
    for repeat_index in range(repeat_count):
        for case in dataset.cases:
            elapsed = time.monotonic() - last_started
            if elapsed < minimum_interval_seconds:
                await asyncio.sleep(minimum_interval_seconds - elapsed)
            last_started = time.monotonic()
            result_started = datetime.now(UTC)
            monotonic_started = time.monotonic()
            try:
                node_result, diagnostics = await node.interpret_with_diagnostics(
                    case.input.to_provider_input()
                )
                completed = datetime.now(UTC)
                latency_ms = int((time.monotonic() - monotonic_started) * 1000)
                scored = score_hybrid_success(
                    case,
                    repeat_index=repeat_index,
                    result=node_result,
                    started_at=result_started,
                    completed_at=completed,
                    latency_ms=latency_ms,
                )
                fact_observations.append((case, diagnostics.facts))
                rejected = diagnostics.facts.rejected_evidence_count
                evidence_count = _evidence_count(diagnostics.facts)
                verification_count = diagnostics.metadata.verification_call_count
            except (LLMProviderError, StructuredOutputInvalid) as exc:
                completed = datetime.now(UTC)
                latency_ms = int((time.monotonic() - monotonic_started) * 1000)
                code = exc.code.value if isinstance(exc, LLMProviderError) else "INVALID_OUTPUT"
                scored = score_failure(
                    case,
                    repeat_index=repeat_index,
                    provider=provider,
                    model=model,
                    prompt_id="resident_fact_extraction",
                    prompt_version="1.0.0",
                    prompt_hash=node._prompts.resident_fact_extraction().prompt_hash,
                    schema_version="resident-facts-v1",
                    started_at=result_started,
                    completed_at=completed,
                    latency_ms=latency_ms,
                    error_code=code,
                    invalid_output=not isinstance(exc, LLMProviderError)
                    or code
                    in {
                        "INVALID_JSON",
                        "SCHEMA_VALIDATION_FAILED",
                        "INVARIANT_VIOLATION",
                    },
                    attempt_count=getattr(exc, "attempt_count", 1),
                    provider_retry_after_seconds=getattr(exc, "retry_after_seconds", None),
                )
                rejected = evidence_count = verification_count = 0
            results.append(scored)
            safe_cases.append(
                HybridSafeCaseSummary(
                    case_id=case.case_id,
                    repeat_index=repeat_index,
                    suite=case.suite.value,
                    severity=case.severity.value,
                    status=scored.status.value,
                    case_passed=scored.case_passed,
                    failure_paths=tuple(
                        item.path
                        for item in scored.matcher_results
                        if item.hard and not item.passed
                    ),
                    failure_details=tuple(
                        f"{item.path}:actual={item.actual!r}:expected={item.expected!r}"
                        for item in scored.matcher_results
                        if item.hard and not item.passed
                    ),
                    critical_failure_codes=tuple(
                        item.value for item in scored.critical_failure_codes
                    ),
                    rejected_evidence_count=rejected,
                    evidence_count=evidence_count,
                    verification_call_count=verification_count,
                    latency_ms=scored.latency_ms,
                    attempt_count=scored.attempt_count,
                    input_tokens=scored.usage.input_tokens,
                    output_tokens=scored.usage.output_tokens,
                    total_tokens=scored.usage.total_tokens,
                    utterance_intent=(
                        scored.interpretation.utterance_intent.value
                        if scored.interpretation is not None
                        else None
                    ),
                    missing_fields=(
                        tuple(
                            sorted(
                                item.value
                                for item in scored.interpretation.model_suggested_missing_fields
                            )
                        )
                        if scored.interpretation is not None
                        else ()
                    ),
                    safety_flags=(
                        tuple(sorted(item.value for item in scored.interpretation.safety_flags))
                        if scored.interpretation is not None
                        else ()
                    ),
                    requested_human=(
                        scored.interpretation.utterance_intent.value == "REQUEST_HUMAN"
                        if scored.interpretation is not None
                        else None
                    ),
                )
            )
            if progress is not None:
                progress(len(results), len(dataset.cases) * repeat_count, scored)
    result_tuple = tuple(results)
    metrics = calculate_hybrid_metrics(dataset.cases, result_tuple)
    gate = (
        evaluate_smoke_gate(metrics)
        if stage in {"PROBE", "SMOKE"}
        else evaluate_development_gate(metrics)
    )
    final_diagnostics = node.last_diagnostics
    if final_diagnostics is None:
        raise RuntimeError("hybrid evaluation produced no successful diagnostics")
    report = HybridEvaluationReport(
        run_id=uuid4(),
        stage=stage,
        development_only=stage in {"PROBE", "SMOKE", "DEVELOPMENT", "HISTORICAL_REGRESSION"},
        challenge_consumed=stage in {"HISTORICAL_REGRESSION", "FORMAL_CHALLENGE"},
        dataset_id=dataset.metadata.dataset_id,
        dataset_version=dataset.metadata.dataset_version,
        dataset_hash=dataset.metadata.dataset_hash,
        provider=provider,
        model=model,
        runtime_commit=runtime_commit,
        source_clean=source_clean,
        architecture=final_diagnostics.metadata,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        evaluation_call_count=len(results),
        upstream_attempt_count=sum(item.attempt_count for item in results),
        verification_call_count=sum(item.verification_call_count for item in safe_cases),
        metrics=metrics,
        fact_metrics=_fact_metrics(fact_observations),
        gate=gate,
        provider_failure_counts=dict(
            sorted(
                Counter(
                    item.provider_error_code
                    for item in results
                    if item.provider_error_code is not None
                ).items()
            )
        ),
        cases=tuple(safe_cases),
    )
    atomic_write_text(output_path, report.model_dump_json(indent=2))
    return report


def compare_hybrid_repeats(
    first: HybridEvaluationReport,
    second: HybridEvaluationReport,
) -> HybridStabilityReport:
    if first.dataset_hash != second.dataset_hash:
        raise ValueError("hybrid repeat dataset hashes do not match")
    if first.architecture != second.architecture:
        raise ValueError("hybrid repeat architecture identities do not match")
    left = {item.case_id: item for item in first.cases}
    right = {item.case_id: item for item in second.cases}
    if len(left) != len(first.cases) or len(right) != len(second.cases):
        raise ValueError("hybrid repeat reports must contain one result per case")
    if set(left) != set(right):
        raise ValueError("hybrid repeat case sets do not match")

    all_ids = tuple(sorted(left))
    critical_ids = tuple(case_id for case_id in all_ids if left[case_id].severity == "CRITICAL")
    human_ids = tuple(case_id for case_id in all_ids if left[case_id].suite == "REQUEST_HUMAN")

    intent = _projected_consistency(all_ids, left, right, lambda item: item.utterance_intent)
    missing = _projected_consistency(all_ids, left, right, lambda item: item.missing_fields)
    safety = _projected_consistency(all_ids, left, right, lambda item: item.safety_flags)
    critical_safety = _projected_consistency(
        critical_ids, left, right, lambda item: item.safety_flags
    )
    request_human = _projected_consistency(
        human_ids, left, right, lambda item: item.requested_human
    )
    rules = (
        ("intent_consistency_rate", intent, 0.98),
        ("clarification_consistency_rate", missing, 0.98),
        ("missing_fields_consistency_rate", missing, 0.95),
        ("safety_consistency_rate", safety, 0.98),
        ("critical_safety_consistency_rate", critical_safety, 1.0),
        ("request_human_consistency_rate", request_human, 1.0),
    )
    failed = tuple(name for name, actual, threshold in rules if actual < threshold)
    return HybridStabilityReport(
        first_run_id=first.run_id,
        second_run_id=second.run_id,
        intent_consistency_rate=intent,
        clarification_consistency_rate=missing,
        missing_fields_consistency_rate=missing,
        safety_consistency_rate=safety,
        critical_safety_consistency_rate=critical_safety,
        request_human_consistency_rate=request_human,
        passed=not failed,
        failed_rules=failed,
    )


def _projected_consistency(
    case_ids: tuple[str, ...],
    first: dict[str, HybridSafeCaseSummary],
    second: dict[str, HybridSafeCaseSummary],
    projection: Callable[[HybridSafeCaseSummary], object],
) -> float:
    if not case_ids:
        return 1.0
    matches = sum(projection(first[case_id]) == projection(second[case_id]) for case_id in case_ids)
    return matches / len(case_ids)


def _evidence_count(facts: NormalizedResidentFactsV1) -> int:
    payload = facts.model_dump(mode="python")
    return sum(
        1 for key, value in payload.items() if key.endswith("_evidence") and value is not None
    ) + len(payload.get("safety_evidence", ()))


_TIME_REFERENCE = re.compile(
    r"(今天|明天|后天|下周|周[一二三四五六日天]|星期[一二三四五六日天]|"
    r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}:\d{2}|上午|下午|晚上|两点|几点)"
)
_TICKET_REFERENCE = re.compile(r"(工单已经有|已有工单|现有.{0,4}工单|之前的工单)")
_APPOINTMENT_REFERENCE = re.compile(r"(之前的预约|原预约|已有预约|上次约|这次上门预约)")


def _fact_metrics(
    observations: list[tuple[EvaluationCase, NormalizedResidentFactsV1]],
) -> HybridFactMetrics:
    if not observations:
        return HybridFactMetrics(
            fact_precision=0,
            fact_recall=0,
            fact_f1=0,
            evidence_span_validity=0,
            explicit_human_request_recall=0,
            existing_ticket_fact_accuracy=0,
            existing_appointment_fact_accuracy=0,
            time_expression_presence_accuracy=0,
            correction_detection_accuracy=0,
            safety_evidence_recall=0,
            unsupported_fact_hallucination_count=0,
        )
    tp = fp = fn = valid_evidence = total_evidence = rejected = 0
    human_tp = human_total = safety_tp = safety_total = 0
    ticket_correct = appointment_correct = time_correct = correction_correct = 0
    for case, facts in observations:
        message = case.input.current_user_message
        expected_human = _expected_bool(case, "$.requested_human")
        expected_safety = bool(_expected_set(case, "$.safety_flags"))
        expected_category = _expected_value(case, "$.issue_category")
        actual_category = (
            facts.issue_category_evidence[0].category.value
            if len(facts.issue_category_evidence) == 1
            else None
        )
        for expected, actual in (
            (expected_human, facts.explicit_human_request),
            (expected_safety, bool(facts.safety_evidence)),
            (expected_category, actual_category),
        ):
            if expected in {None, False} and actual in {None, False}:
                continue
            if expected == actual:
                tp += 1
            elif actual in {None, False}:
                fn += 1
            else:
                fp += 1
        if expected_human:
            human_total += 1
            human_tp += facts.explicit_human_request
        if expected_safety:
            safety_total += 1
            safety_tp += bool(facts.safety_evidence)
        ticket_correct += facts.existing_ticket_mentioned == bool(_TICKET_REFERENCE.search(message))
        appointment_correct += facts.existing_appointment_mentioned == bool(
            _APPOINTMENT_REFERENCE.search(message)
        )
        time_correct += facts.time_expression_present == bool(_TIME_REFERENCE.search(message))
        correction_correct += facts.correction_present == (
            case.suite.value == "MULTI_TURN_CORRECTION"
        )
        evidence = _evidence_count(facts)
        total_evidence += evidence + facts.rejected_evidence_count
        valid_evidence += evidence
        rejected += facts.rejected_evidence_count
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return HybridFactMetrics(
        fact_precision=precision,
        fact_recall=recall,
        fact_f1=2 * precision * recall / (precision + recall) if precision + recall else 0,
        evidence_span_validity=valid_evidence / total_evidence if total_evidence else 1,
        explicit_human_request_recall=human_tp / human_total if human_total else 1,
        existing_ticket_fact_accuracy=ticket_correct / len(observations),
        existing_appointment_fact_accuracy=appointment_correct / len(observations),
        time_expression_presence_accuracy=time_correct / len(observations),
        correction_detection_accuracy=correction_correct / len(observations),
        safety_evidence_recall=safety_tp / safety_total if safety_total else 1,
        unsupported_fact_hallucination_count=rejected,
    )


def _expected_value(case: EvaluationCase, path: str) -> object:
    return next((item.value for item in case.expected.fields if item.path == path), None)


def _expected_bool(case: EvaluationCase, path: str) -> bool:
    return bool(_expected_value(case, path))


def _expected_set(case: EvaluationCase, path: str) -> set[str]:
    value = _expected_value(case, path)
    return set(value) if isinstance(value, tuple) else set()
