"""Development-corpus regression for deterministic hybrid decisions.

The fact provider is deliberately empty: this test proves the deterministic
fallback and final contract, not online fact-extraction quality.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.llm.evaluation.dataset_loader import load_dataset
from app.llm.evaluation.scorer_v2 import score_success
from app.llm.hybrid.pipeline import HybridInterpretationNode
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
        scored = score_success(
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
