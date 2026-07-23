from pathlib import Path

import pytest
from app.llm.evaluation.dataset_loader import load_dataset, load_policy
from app.llm.evaluation.models import EvaluationDataset, EvaluationGatePolicy


@pytest.fixture(scope="session")
def eval_root() -> Path:
    return Path(__file__).resolve().parents[3] / "evals"


@pytest.fixture(scope="session")
def release_dataset(eval_root: Path) -> EvaluationDataset:
    return load_dataset(eval_root / "datasets" / "resident_interpretation_v1.jsonl")


@pytest.fixture(scope="session")
def gate_policy(eval_root: Path) -> EvaluationGatePolicy:
    return load_policy(eval_root / "policies" / "resident_interpretation_gate_v1.json")
