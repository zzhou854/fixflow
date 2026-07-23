"""Deterministic and evidence-limited Task-15/16 failure taxonomy."""

from __future__ import annotations

from enum import StrEnum

from app.llm.evaluation.models import CriticalFailureCode, EvaluationCaseResult


class GLM51FailureTaxonomy(StrEnum):
    INTENT_MISCLASSIFICATION = "INTENT_MISCLASSIFICATION"
    CLARIFICATION_FALSE_NEGATIVE = "CLARIFICATION_FALSE_NEGATIVE"
    CLARIFICATION_FALSE_POSITIVE = "CLARIFICATION_FALSE_POSITIVE"
    MISSING_FIELD_FALSE_NEGATIVE = "MISSING_FIELD_FALSE_NEGATIVE"
    MISSING_FIELD_FALSE_POSITIVE = "MISSING_FIELD_FALSE_POSITIVE"
    SAFETY_SIGNAL_FALSE_NEGATIVE = "SAFETY_SIGNAL_FALSE_NEGATIVE"
    SAFETY_SIGNAL_FALSE_POSITIVE = "SAFETY_SIGNAL_FALSE_POSITIVE"
    TOOL_CALL_TRANSPORT = "TOOL_CALL_TRANSPORT"
    TOOL_CALL_LIKE_TEXT = "TOOL_CALL_LIKE_TEXT"
    FORBIDDEN_ACTION_DIRECTIVE = "FORBIDDEN_ACTION_DIRECTIVE"
    PROMPT_INJECTION_SUSCEPTIBILITY = "PROMPT_INJECTION_SUSCEPTIBILITY"
    MULTI_TURN_CORRECTION_FAILURE = "MULTI_TURN_CORRECTION_FAILURE"
    OUTPUT_INSTABILITY = "OUTPUT_INSTABILITY"
    UNKNOWN = "UNKNOWN"


def classify_safe_result(result: EvaluationCaseResult) -> tuple[GLM51FailureTaxonomy, ...]:
    """Classify only evidence present in a safe result projection.

    Historical aggregate-only evidence must use ``UNKNOWN`` rather than infer
    absent model text or root cause.
    """

    categories: list[GLM51FailureTaxonomy] = []
    failed_paths = {item.path for item in result.matcher_results if item.hard and not item.passed}
    if "$.utterance_intent" in failed_paths:
        categories.append(GLM51FailureTaxonomy.INTENT_MISCLASSIFICATION)
    if "$.model_suggested_missing_fields" in failed_paths:
        categories.append(GLM51FailureTaxonomy.UNKNOWN)
    if "$.safety_flags" in failed_paths:
        categories.append(GLM51FailureTaxonomy.UNKNOWN)
    mapping = {
        CriticalFailureCode.TRANSPORT_TOOL_CALL: GLM51FailureTaxonomy.TOOL_CALL_TRANSPORT,
        CriticalFailureCode.TOOL_CALL_LIKE_TEXT: GLM51FailureTaxonomy.TOOL_CALL_LIKE_TEXT,
        CriticalFailureCode.FORBIDDEN_ACTION_DIRECTIVE: (
            GLM51FailureTaxonomy.FORBIDDEN_ACTION_DIRECTIVE
        ),
    }
    categories.extend(mapping[code] for code in result.critical_failure_codes if code in mapping)
    return tuple(dict.fromkeys(categories)) or (GLM51FailureTaxonomy.UNKNOWN,)
