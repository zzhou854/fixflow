"""Deterministic, non-semantic value normalization."""

import re
import unicodedata

from app.llm.evaluation.models import EvaluationNormalizer, ExpectedValue


def normalize(value: ExpectedValue, kind: EvaluationNormalizer) -> ExpectedValue:
    if kind is EvaluationNormalizer.NONE or value is None:
        return value
    if kind is EvaluationNormalizer.STRING_SET:
        if isinstance(value, tuple):
            return tuple(sorted(set(item.strip() for item in value)))
        if isinstance(value, str):
            return tuple(sorted({part.strip() for part in value.split(",") if part.strip()}))
        return value
    if not isinstance(value, str):
        return value
    if kind is EvaluationNormalizer.TRIM:
        return value.strip()
    if kind is EvaluationNormalizer.CASEFOLD:
        return value.casefold()
    if kind is EvaluationNormalizer.WHITESPACE:
        return " ".join(value.split())
    if kind is EvaluationNormalizer.CHINESE_PUNCTUATION:
        normalized = unicodedata.normalize("NFKC", value)
        return re.sub(r"[,。!?;:、“”‘’]", "", normalized).strip()
    return value
