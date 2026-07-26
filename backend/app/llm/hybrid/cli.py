"""Explicit online-development CLI for hybrid interpretation."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import subprocess
from pathlib import Path

from app.config import Settings
from app.llm.evaluation.challenge import validate_challenge_isolation
from app.llm.evaluation.dataset_loader import load_dataset
from app.llm.evaluation.models import EvaluationCaseResult, EvaluationDataset
from app.llm.hybrid.evaluation import HybridStage, run_hybrid_evaluation
from app.llm.hybrid.pipeline import HybridInterpretationNode
from app.llm.hybrid.prompts import FactPromptRegistry
from app.llm.providers.deepseek import (
    DeepSeekConfig,
    DeepSeekStructuredInterpretationProvider,
)
from app.llm.validation.parser import StructuredInterpretationParser

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = ROOT / "evals" / "datasets" / "resident_interpretation_v1.jsonl"
CHALLENGE_DATASET = ROOT / "evals" / "datasets" / "resident_interpretation_challenge_v1.jsonl"
SMOKE_DEFINITION = ROOT / "evals" / "development" / "resident_interpretation_prompt_v2_smoke.json"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="fixflow-hybrid-eval")
    root.add_argument(
        "--stage",
        choices=(
            "probe",
            "smoke",
            "development",
            "formal-regression",
            "formal-challenge",
        ),
        required=True,
    )
    root.add_argument(
        "--model",
        choices=("deepseek-v4-flash", "deepseek-v4-pro"),
        required=True,
    )
    root.add_argument("--output", type=Path, required=True)
    root.add_argument("--repeats", type=int, choices=(1, 2), default=1)
    root.add_argument("--probe-count", type=int, choices=range(1, 11), default=5)
    root.add_argument("--minimum-interval-seconds", type=float, default=0.25)
    root.add_argument("--allow-network", action="store_true")
    root.add_argument("--acknowledge-cost", action="store_true")
    root.add_argument("--runtime-commit")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.allow_network or not args.acknowledge_cost:
        raise SystemExit("online hybrid evaluation requires both explicit network and cost flags")
    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    runtime_commit, source_clean = _formal_source_identity(args.stage, args.runtime_commit)
    settings = Settings(llm_provider="deepseek", deepseek_model=args.model)
    assert settings.deepseek_api_key is not None
    dataset = _dataset(args.stage, args.probe_count)
    prompt = FactPromptRegistry().resident_fact_extraction()
    provider = DeepSeekStructuredInterpretationProvider(
        prompt=prompt,
        parser=StructuredInterpretationParser(max_response_bytes=settings.llm_max_response_bytes),
        config=DeepSeekConfig(
            model=args.model,
            base_url=settings.deepseek_base_url,
            thinking_mode="disabled",
            temperature=0,
            top_p=1,
            max_tokens=settings.deepseek_max_tokens,
            request_timeout_seconds=max(30, settings.deepseek_request_timeout_seconds),
            total_timeout_seconds=max(45, settings.deepseek_total_timeout_seconds),
            max_attempts=1,
            max_concurrency=1,
        ),
        api_key=settings.deepseek_api_key.get_secret_value(),
    )
    node = HybridInterpretationNode(provider, model=args.model)
    try:
        stage: HybridStage
        if args.stage == "probe":
            stage = "PROBE"
        elif args.stage == "smoke":
            stage = "SMOKE"
        elif args.stage == "development":
            stage = "DEVELOPMENT"
        elif args.stage == "formal-regression":
            stage = "FORMAL_REGRESSION"
        else:
            stage = "FORMAL_CHALLENGE"
        report = await run_hybrid_evaluation(
            dataset=dataset,
            node=node,
            provider="deepseek",
            model=args.model,
            stage=stage,
            repeat_count=args.repeats,
            output_path=args.output,
            minimum_interval_seconds=args.minimum_interval_seconds,
            runtime_commit=runtime_commit,
            source_clean=source_clean,
            progress=_progress,
        )
    finally:
        await provider.close()
    summary = {
        "run_id": str(report.run_id),
        "stage": report.stage,
        "provider": report.provider,
        "model": report.model,
        "architecture_version": report.architecture.architecture_version,
        "fact_prompt_hash": report.architecture.fact_prompt_hash,
        "dataset_id": report.dataset_id,
        "dataset_hash": report.dataset_hash,
        "calls": report.evaluation_call_count,
        "gate_passed": report.gate.passed,
        "failed_rules": report.gate.failed_rules,
        "metrics": report.metrics.model_dump(mode="json"),
        "fact_metrics": report.fact_metrics.model_dump(mode="json"),
        "provider_failure_counts": report.provider_failure_counts,
        "sdk": f"httpx@{importlib.metadata.version('httpx')}",
        "runtime_commit": report.runtime_commit,
        "source_clean": report.source_clean,
        "challenge_live_calls": (
            report.evaluation_call_count if report.stage == "FORMAL_CHALLENGE" else 0
        ),
        "artifact": str(args.output),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if report.gate.passed else 2


def _dataset(stage: str, probe_count: int) -> EvaluationDataset:
    dataset = load_dataset(DEFAULT_DATASET)
    if stage in {"development", "formal-regression"}:
        return dataset
    if stage == "formal-challenge":
        challenge = load_dataset(CHALLENGE_DATASET)
        validate_challenge_isolation(
            challenge,
            regression=dataset,
            prompts=(),
        )
        return challenge
    if stage == "smoke":
        definition = json.loads(SMOKE_DEFINITION.read_text(encoding="utf-8"))
        case_ids = set(definition["case_ids"])
        cases = tuple(case for case in dataset.cases if case.case_id in case_ids)
    else:
        cases = dataset.cases[:probe_count]
    metadata = dataset.metadata.model_copy(
        update={
            "case_count": len(cases),
            # The parent hash remains the immutable source identity; the stage
            # and selected case IDs are separately present in the report.
        }
    )
    return EvaluationDataset(metadata=metadata, cases=cases)


def _formal_source_identity(
    stage: str,
    requested_commit: str | None,
) -> tuple[str | None, bool]:
    if not stage.startswith("formal-"):
        if requested_commit is not None:
            raise ValueError("--runtime-commit is only valid for formal stages")
        return None, False
    if requested_commit is None:
        raise ValueError("formal evaluation requires --runtime-commit")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        raise RuntimeError("formal evaluation requires a clean working tree")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT.parent,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if requested_commit != head:
        raise RuntimeError("formal runtime commit does not match HEAD")
    return head, True


def _progress(done: int, total: int, result: EvaluationCaseResult) -> None:
    print(f"progress={done}/{total} status={result.status.value} case={result.case_id}")


if __name__ == "__main__":
    raise SystemExit(main())
