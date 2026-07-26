"""Development-corpus regression for deterministic hybrid decisions.

The fact provider is deliberately empty: this test proves the deterministic
fallback and final contract, not online fact-extraction quality.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.llm.evaluation.dataset_loader import load_dataset
from app.llm.hybrid.pipeline import HybridInterpretationNode
from app.llm.hybrid.scorer import score_hybrid_success
from tests.unit.llm.hybrid.conftest import empty_facts
from tests.unit.llm.hybrid.test_pipeline import FactProvider


@pytest.mark.asyncio
async def test_deterministic_hybrid_decisions_preserve_development_golden_contract() -> None:
    dataset = load_dataset(Path("backend/evals/datasets/resident_interpretation_v1.jsonl"))
    node = HybridInterpretationNode(FactProvider(empty_facts()), model="empty-fact-fixture")
    failures: list[str] = []
    for case in dataset.cases:
        result = await node(case.input.to_provider_input())
        now = datetime.now(UTC)
        scored = score_hybrid_success(
            case,
            repeat_index=0,
            result=result,
            started_at=now,
            completed_at=now,
            latency_ms=0,
        )
        if not scored.case_passed:
            failures.append(case.case_id)
    assert failures == []


@pytest.mark.asyncio
async def test_consumed_challenge_corpora_are_historical_regression_only() -> None:
    node = HybridInterpretationNode(FactProvider(empty_facts()), model="empty-fact-fixture")
    expected_failures = {
        "resident_interpretation_challenge_v1.jsonl": set(),
        # These v2 labels were copied incorrectly during corpus authoring and
        # were frozen by the first formal call.  They remain immutable failure
        # evidence rather than rules the architecture should imitate.
        "resident_interpretation_challenge_v2.jsonl": {
            "challenge2-missing-009",
            "challenge2-correction-003",
        },
        # This message explicitly says "卧室门锁"; the frozen v3 Golden
        # incorrectly requires ISSUE_LOCATION to remain missing.
        "resident_interpretation_challenge_v3.jsonl": {
            "challenge3-missing-009",
        },
        "resident_interpretation_challenge_v4.jsonl": set(),
        "resident_interpretation_challenge_v5.jsonl": set(),
    }
    for filename, expected in expected_failures.items():
        dataset = load_dataset(Path("backend/evals/datasets") / filename)
        failures: set[str] = set()
        for case in dataset.cases:
            result = await node(case.input.to_provider_input())
            now = datetime.now(UTC)
            scored = score_hybrid_success(
                case,
                repeat_index=0,
                result=result,
                started_at=now,
                completed_at=now,
                latency_ms=0,
            )
            if not scored.case_passed:
                failures.add(case.case_id)
        assert failures == expected, "\n".join(sorted(failures))
