"""Frozen-case deterministic retrieval metrics without an LLM judge."""

import json
from datetime import datetime
from pathlib import Path

from pydantic import Field

from app.domain.enums import IssueCategory
from app.policy.enums import EvidenceSufficiency, PolicyTopic
from app.policy.models import PolicyModel, PolicyRetrievalResult, require_aware


class PolicyEvaluationCase(PolicyModel):
    case_id: str = Field(min_length=1, max_length=80)
    query_text: str = Field(min_length=1, max_length=2000)
    issue_category: IssueCategory
    policy_topics: tuple[PolicyTopic, ...] = Field(min_length=1)
    as_of: datetime
    expected_policy_codes: tuple[str, ...]
    forbidden_policy_codes: tuple[str, ...]
    expired_policy_codes: tuple[str, ...] = ()
    expected_sufficiency: EvidenceSufficiency
    expected_conflict: bool

    def model_post_init(self, context: object) -> None:
        del context
        require_aware(self.as_of, "as_of")


class PolicyEvaluationSet(PolicyModel):
    cases: tuple[PolicyEvaluationCase, ...] = Field(min_length=20)


class PolicyEvaluationMetrics(PolicyModel):
    case_count: int
    recall_at_k: float
    mean_reciprocal_rank: float
    forbidden_policy_retrieval_rate: float
    expired_policy_retrieval_rate: float
    conflict_detection_accuracy: float
    sufficiency_accuracy: float


def load_policy_evaluation_set(path: Path) -> PolicyEvaluationSet:
    return PolicyEvaluationSet.model_validate(json.loads(path.read_text(encoding="utf-8")))


def calculate_policy_metrics(
    cases: tuple[PolicyEvaluationCase, ...],
    results: tuple[PolicyRetrievalResult, ...],
) -> PolicyEvaluationMetrics:
    if not cases or len(cases) != len(results):
        raise ValueError("cases and results must have the same non-zero length")
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    forbidden_cases = 0
    expired_cases = 0
    conflict_correct = 0
    sufficiency_correct = 0
    for case, result in zip(cases, results, strict=True):
        codes = [item.policy_code for item in result.evidence]
        expected = set(case.expected_policy_codes)
        found = expected.intersection(codes)
        recalls.append(len(found) / len(expected) if expected else 1.0)
        relevant_ranks = [index for index, code in enumerate(codes, 1) if code in expected]
        reciprocal_ranks.append(1 / min(relevant_ranks) if relevant_ranks else 0.0)
        forbidden_cases += int(bool(set(case.forbidden_policy_codes).intersection(codes)))
        expired_cases += int(bool(set(case.expired_policy_codes).intersection(codes)))
        conflict_correct += int(bool(result.conflicts) is case.expected_conflict)
        sufficiency_correct += int(result.sufficiency is case.expected_sufficiency)
    count = len(cases)
    return PolicyEvaluationMetrics(
        case_count=count,
        recall_at_k=sum(recalls) / count,
        mean_reciprocal_rank=sum(reciprocal_ranks) / count,
        forbidden_policy_retrieval_rate=forbidden_cases / count,
        expired_policy_retrieval_rate=expired_cases / count,
        conflict_detection_accuracy=conflict_correct / count,
        sufficiency_accuracy=sufficiency_correct / count,
    )
