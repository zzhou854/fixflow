"""Restricted path resolution and deterministic field matchers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.llm.evaluation.models import (
    EvaluationMatcher,
    ExpectedValue,
    FieldExpectation,
    MatcherResult,
)
from app.llm.evaluation.normalizers import normalize

MISSING = object()


def resolve_path(payload: Mapping[str, object], path: str) -> object:
    current: object = payload
    for part in path.removeprefix("$.").split("."):
        if not isinstance(current, Mapping) or part not in current:
            return MISSING
        current = current[part]
    return current


def _safe(value: object) -> ExpectedValue:
    if value is MISSING:
        return "<missing>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(str(item) for item in value)
    return f"<{type(value).__name__}>"


def match(expectation: FieldExpectation, payload: Mapping[str, object]) -> MatcherResult:
    raw_actual = resolve_path(payload, expectation.path)
    actual = _safe(raw_actual)
    expected = expectation.value
    normalized_actual = normalize(actual, expectation.normalizer)
    normalized_expected = normalize(expected, expectation.normalizer)
    passed, reason = _evaluate(expectation, normalized_actual, normalized_expected)
    return MatcherResult(
        path=expectation.path,
        matcher=expectation.matcher,
        passed=passed,
        hard=expectation.hard,
        expected=expected,
        actual=actual,
        reason=reason,
    )


def _evaluate(
    expectation: FieldExpectation,
    actual: ExpectedValue,
    expected: ExpectedValue,
) -> tuple[bool, str]:
    matcher = expectation.matcher
    if matcher is EvaluationMatcher.EXACT:
        passed = actual == expected
    elif matcher is EvaluationMatcher.ONE_OF:
        passed = isinstance(expected, tuple) and str(actual) in expected
    elif matcher is EvaluationMatcher.NULL:
        passed = actual is None
    elif matcher is EvaluationMatcher.NON_NULL:
        passed = actual is not None and actual != "<missing>"
    elif matcher is EvaluationMatcher.EMPTY:
        passed = actual in ("", (), None)
    elif matcher is EvaluationMatcher.NON_EMPTY:
        passed = actual not in ("", (), None, "<missing>")
    elif matcher in {
        EvaluationMatcher.SET_EXACT,
        EvaluationMatcher.SET_CONTAINS,
        EvaluationMatcher.SET_EXCLUDES,
    }:
        actual_set = set(actual) if isinstance(actual, tuple) else set()
        expected_set = set(expected) if isinstance(expected, tuple) else set()
        if matcher is EvaluationMatcher.SET_EXACT:
            passed = actual_set == expected_set
        elif matcher is EvaluationMatcher.SET_CONTAINS:
            passed = expected_set <= actual_set
        else:
            passed = actual_set.isdisjoint(expected_set)
    elif matcher is EvaluationMatcher.STRING_CONTAINS:
        passed = isinstance(actual, str) and isinstance(expected, str) and expected in actual
    elif matcher is EvaluationMatcher.STRING_EXCLUDES:
        passed = isinstance(actual, str) and isinstance(expected, str) and expected not in actual
    elif matcher is EvaluationMatcher.REGEX:
        passed = (
            isinstance(actual, str)
            and isinstance(expected, str)
            and re.search(expected, actual) is not None
        )
    elif matcher is EvaluationMatcher.NUMBER_RANGE:
        passed = (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and expectation.minimum is not None
            and expectation.maximum is not None
            and expectation.minimum <= actual <= expectation.maximum
        )
    elif matcher is EvaluationMatcher.BOOLEAN:
        passed = isinstance(actual, bool) and actual is expected
    else:
        passed = False
    return passed, "matched" if passed else "deterministic matcher failed"
