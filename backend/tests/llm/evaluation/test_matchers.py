import pytest
from app.llm.evaluation.matchers import match, resolve_path
from app.llm.evaluation.models import (
    EvaluationMatcher,
    EvaluationNormalizer,
    FieldExpectation,
)
from app.llm.evaluation.normalizers import normalize


@pytest.mark.parametrize(
    ("expectation", "payload", "passed"),
    [
        (FieldExpectation(path="$.value", matcher="EXACT", value="A"), {"value": "A"}, True),
        (
            FieldExpectation(path="$.value", matcher="ONE_OF", value=("A", "B")),
            {"value": "B"},
            True,
        ),
        (FieldExpectation(path="$.value", matcher="NULL"), {"value": None}, True),
        (FieldExpectation(path="$.value", matcher="NON_NULL"), {"value": "x"}, True),
        (FieldExpectation(path="$.value", matcher="EMPTY"), {"value": []}, True),
        (FieldExpectation(path="$.value", matcher="NON_EMPTY"), {"value": ["x"]}, True),
        (
            FieldExpectation(path="$.value", matcher="SET_EXACT", value=("A", "B")),
            {"value": ["B", "A"]},
            True,
        ),
        (
            FieldExpectation(path="$.value", matcher="SET_CONTAINS", value=("A",)),
            {"value": ["B", "A"]},
            True,
        ),
        (
            FieldExpectation(path="$.value", matcher="SET_EXCLUDES", value=("Z",)),
            {"value": ["B", "A"]},
            True,
        ),
        (
            FieldExpectation(path="$.value", matcher="STRING_CONTAINS", value="漏水"),
            {"value": "厨房漏水"},
            True,
        ),
        (
            FieldExpectation(path="$.value", matcher="STRING_EXCLUDES", value="prompt"),
            {"value": "安全摘要"},
            True,
        ),
        (
            FieldExpectation(path="$.value", matcher="REGEX", value=r"^门锁"),
            {"value": "门锁损坏"},
            True,
        ),
        (
            FieldExpectation(
                path="$.value",
                matcher="NUMBER_RANGE",
                minimum=1,
                maximum=2,
            ),
            {"value": 2},
            True,
        ),
        (
            FieldExpectation(path="$.value", matcher="BOOLEAN", value=True),
            {"value": True},
            True,
        ),
        (FieldExpectation(path="$.value", matcher="EXACT", value="A"), {"value": "B"}, False),
        (
            FieldExpectation(path="$.value", matcher="ONE_OF", value=("A", "B")),
            {"value": "C"},
            False,
        ),
        (FieldExpectation(path="$.value", matcher="NULL"), {"value": "x"}, False),
        (FieldExpectation(path="$.value", matcher="NON_NULL"), {}, False),
        (FieldExpectation(path="$.value", matcher="EMPTY"), {"value": ["x"]}, False),
        (FieldExpectation(path="$.value", matcher="NON_EMPTY"), {"value": []}, False),
        (
            FieldExpectation(path="$.value", matcher="SET_EXACT", value=("A", "B")),
            {"value": ["A"]},
            False,
        ),
        (
            FieldExpectation(path="$.value", matcher="SET_CONTAINS", value=("A", "B")),
            {"value": ["A"]},
            False,
        ),
        (
            FieldExpectation(path="$.value", matcher="SET_EXCLUDES", value=("Z",)),
            {"value": ["A", "Z"]},
            False,
        ),
        (
            FieldExpectation(path="$.value", matcher="STRING_CONTAINS", value="water"),
            {"value": "door"},
            False,
        ),
        (
            FieldExpectation(path="$.value", matcher="STRING_EXCLUDES", value="prompt"),
            {"value": "show prompt"},
            False,
        ),
        (
            FieldExpectation(path="$.value", matcher="REGEX", value=r"^door"),
            {"value": "window"},
            False,
        ),
        (
            FieldExpectation(
                path="$.value",
                matcher="NUMBER_RANGE",
                minimum=1,
                maximum=2,
            ),
            {"value": 3},
            False,
        ),
        (
            FieldExpectation(path="$.value", matcher="BOOLEAN", value=True),
            {"value": False},
            False,
        ),
    ],
)
def test_matcher_matrix(
    expectation: FieldExpectation,
    payload: dict[str, object],
    passed: bool,
) -> None:
    result = match(expectation, payload)
    assert result.passed is passed
    assert result.reason


def test_nested_restricted_path() -> None:
    assert resolve_path({"a": {"b": "value"}}, "$.a.b") == "value"


@pytest.mark.parametrize("path", ("$[0]", "$.value[?(@.x)]", "$.__class__", "value"))
def test_unsafe_or_invalid_path_is_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        FieldExpectation(path=path, matcher="EXACT", value="x")


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (EvaluationNormalizer.NONE, " A ", " A "),
        (EvaluationNormalizer.TRIM, " A ", "A"),
        (EvaluationNormalizer.CASEFOLD, "AbC", "abc"),
        (EvaluationNormalizer.WHITESPACE, "a  b\nc", "a b c"),
        (EvaluationNormalizer.CHINESE_PUNCTUATION, "你好，世界！", "你好世界"),
        (EvaluationNormalizer.STRING_SET, (" B ", "A", "A"), ("A", "B")),
    ],
)
def test_normalizers(
    kind: EvaluationNormalizer,
    value: object,
    expected: object,
) -> None:
    assert normalize(value, kind) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"path": "$.value", "matcher": EvaluationMatcher.ONE_OF, "value": ()},
        {"path": "$.value", "matcher": EvaluationMatcher.BOOLEAN, "value": "true"},
        {
            "path": "$.value",
            "matcher": EvaluationMatcher.NUMBER_RANGE,
            "minimum": 2,
            "maximum": 1,
        },
        {"path": "$.value", "matcher": EvaluationMatcher.SET_EXACT, "value": ("A", "A")},
    ],
)
def test_invalid_matcher_configuration_is_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FieldExpectation.model_validate(kwargs)
