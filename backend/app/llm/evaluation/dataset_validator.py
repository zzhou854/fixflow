"""Dataset identity, golden-label, privacy, and distribution validation."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from app.agent.enums import AgentIntent, IssueField, SafetyFlag
from app.domain.enums import IssueCategory
from app.llm.evaluation.errors import DatasetValidationError, PolicyValidationError
from app.llm.evaluation.hashing import dataset_hash, policy_hash
from app.llm.evaluation.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationGatePolicy,
    EvaluationMatcher,
    EvaluationSeverity,
    EvaluationSuite,
)
from app.llm.prompts.registry import PromptDefinition

EXPECTED_SUITE_COUNTS: dict[EvaluationSuite, int] = {
    EvaluationSuite.CREATE_TICKET: 18,
    EvaluationSuite.NEED_INFORMATION: 18,
    EvaluationSuite.BOOK_APPOINTMENT: 14,
    EvaluationSuite.RESCHEDULE_APPOINTMENT: 12,
    EvaluationSuite.REQUEST_HUMAN: 10,
    EvaluationSuite.SAFETY: 16,
    EvaluationSuite.SMALL_TALK: 10,
    EvaluationSuite.UNSUPPORTED: 8,
    EvaluationSuite.ADVERSARIAL: 8,
    EvaluationSuite.MULTI_TURN_CORRECTION: 6,
}
CHALLENGE_SUITE_COUNTS: dict[EvaluationSuite, int] = {
    EvaluationSuite.CREATE_TICKET: 6,
    EvaluationSuite.NEED_INFORMATION: 10,
    EvaluationSuite.BOOK_APPOINTMENT: 6,
    EvaluationSuite.RESCHEDULE_APPOINTMENT: 6,
    EvaluationSuite.REQUEST_HUMAN: 6,
    EvaluationSuite.SAFETY: 10,
    EvaluationSuite.SMALL_TALK: 2,
    EvaluationSuite.UNSUPPORTED: 2,
    EvaluationSuite.ADVERSARIAL: 6,
    EvaluationSuite.MULTI_TURN_CORRECTION: 6,
}

SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mainland mobile number", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("email address", re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    ("bearer token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}")),
    ("api key", re.compile(r"(?i)\b(?:api[_-]?key|GLM_API_KEY)\s*[=:]\s*\S{8,}")),
    ("identity number", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("database url", re.compile(r"(?i)\bpostgres(?:ql)?://\S+:\S+@")),
    (
        "private ip credentials",
        re.compile(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\S+:\S+@"),
    ),
)

ENUM_PATHS: dict[str, type[object]] = {
    "$.utterance_intent": AgentIntent,
    "$.issue_category": IssueCategory,
    "$.safety_flags": SafetyFlag,
    "$.model_suggested_missing_fields": IssueField,
}
BOOLEAN_PATHS = {"$.user_correction", "$.requested_human"}
OUTPUT_PATHS = {
    "$.utterance_intent",
    "$.issue_category",
    "$.issue_location",
    "$.issue_description_update",
    "$.safety_flags",
    "$.user_availability_windows",
    "$.user_correction",
    "$.acceptance_decision",
    "$.requested_human",
    "$.explicit_property_reference",
    "$.model_suggested_missing_fields",
}


class DatasetValidator:
    def validate(
        self,
        dataset: EvaluationDataset,
        *,
        prompt: PromptDefinition,
        require_release_distribution: bool = True,
    ) -> None:
        errors: list[str] = []
        cases = dataset.cases
        identifiers = [case.case_id for case in cases]
        if len(identifiers) != len(set(identifiers)):
            errors.append("case_id values must be unique")
        if dataset.metadata.case_count != len(cases):
            errors.append("manifest case_count does not match JSONL")
        expected_case_file = (
            "resident_interpretation_challenge_"
            f"v{dataset.metadata.dataset_version.split('.', maxsplit=1)[0]}.jsonl"
            if dataset.metadata.dataset_id == "resident_interpretation_challenge"
            else "resident_interpretation_v1.jsonl"
        )
        if dataset.metadata.case_file != expected_case_file:
            errors.append("manifest case_file is not the frozen case filename")
        actual_hash = dataset_hash(dataset.metadata, cases)
        if dataset.metadata.dataset_hash != actual_hash:
            errors.append("dataset_hash does not match canonical dataset content")
        if require_release_distribution:
            self._validate_distribution(cases, errors)
        elif dataset.metadata.dataset_id == "resident_interpretation_challenge":
            self._validate_challenge_distribution(cases, errors)
        self._validate_cases(cases, prompt, errors)
        if errors:
            raise DatasetValidationError("; ".join(errors[:20]))

    @staticmethod
    def _validate_distribution(cases: tuple[EvaluationCase, ...], errors: list[str]) -> None:
        if len(cases) != 120:
            errors.append("release dataset must contain exactly 120 cases")
        counts = Counter(case.suite for case in cases)
        if counts != Counter(EXPECTED_SUITE_COUNTS):
            errors.append("suite distribution does not match the frozen 120-case matrix")
        critical = sum(case.severity is EvaluationSeverity.CRITICAL for case in cases)
        if critical != 16:
            errors.append("release dataset must contain exactly 16 critical cases")
        injection = sum("prompt-injection" in case.tags for case in cases)
        if injection < 8:
            errors.append("release dataset must contain at least 8 prompt-injection cases")
        multi_turn = sum(bool(case.input.recent_conversation_messages) for case in cases)
        if multi_turn < 18:
            errors.append("release dataset must contain at least 18 multi-turn cases")

    @staticmethod
    def _validate_challenge_distribution(
        cases: tuple[EvaluationCase, ...],
        errors: list[str],
    ) -> None:
        if len(cases) != 60:
            errors.append("challenge dataset must contain exactly 60 cases")
        if Counter(case.suite for case in cases) != Counter(CHALLENGE_SUITE_COUNTS):
            errors.append("suite distribution does not match the locked challenge matrix")

    def _validate_cases(
        self,
        cases: Iterable[EvaluationCase],
        prompt: PromptDefinition,
        errors: list[str],
    ) -> None:
        prompt_inputs = {
            str(
                example.input.get(
                    "current_user_message",
                    example.input.get("current_message", ""),
                )
            )
            for example in prompt.examples
            if example.input.get("current_user_message") or example.input.get("current_message")
        }
        for case in cases:
            if case.input.current_user_message in prompt_inputs:
                errors.append(f"{case.case_id}: current message duplicates a prompt example")
            self._scan_sensitive(case, errors)
            for expectation in case.expected.fields:
                if expectation.path not in OUTPUT_PATHS:
                    errors.append(f"{case.case_id}: unsupported output path {expectation.path}")
                    continue
                self._validate_expected_value(case, expectation.path, expectation.value, errors)
                if expectation.matcher is EvaluationMatcher.REGEX:
                    if not isinstance(expectation.value, str):
                        errors.append(f"{case.case_id}: REGEX requires a string")
                    elif len(expectation.value) > 256:
                        errors.append(f"{case.case_id}: regular expression exceeds 256 characters")
                    else:
                        try:
                            re.compile(expectation.value)
                        except re.error:
                            errors.append(f"{case.case_id}: invalid regular expression")

    @staticmethod
    def _scan_sensitive(case: EvaluationCase, errors: list[str]) -> None:
        content = " ".join(
            [
                case.input.current_user_message,
                *(message.content for message in case.input.recent_conversation_messages),
                case.description,
            ]
        )
        for label, pattern in SENSITIVE_PATTERNS:
            if pattern.search(content):
                errors.append(f"{case.case_id}: possible {label}")

    @staticmethod
    def _validate_expected_value(
        case: EvaluationCase,
        path: str,
        value: object,
        errors: list[str],
    ) -> None:
        enum_type = ENUM_PATHS.get(path)
        if enum_type is not None and value is not None:
            values = value if isinstance(value, tuple) else (value,)
            valid = {str(item.value) for item in enum_type}  # type: ignore[attr-defined]
            if any(item not in valid for item in values):
                errors.append(f"{case.case_id}: invalid formal enum value for {path}")
        if path in BOOLEAN_PATHS and value is not None and not isinstance(value, bool):
            errors.append(f"{case.case_id}: {path} requires a boolean expectation")


def validate_policy(policy: EvaluationGatePolicy) -> None:
    if policy.policy_hash != policy_hash(policy):
        raise PolicyValidationError("policy_hash does not match canonical policy content")
    metrics = [rule.metric for rule in (*policy.absolute_rules, *policy.relative_rules)]
    if len(metrics) != len(
        set(
            (rule.operator, rule.metric)
            for rule in (*policy.absolute_rules, *policy.relative_rules)
        )
    ):
        raise PolicyValidationError("policy contains duplicate rule identity")
