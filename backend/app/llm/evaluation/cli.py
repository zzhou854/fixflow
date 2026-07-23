"""Local-only evaluation CLI with stable exit codes and explicit online opt-in."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import ValidationError

from app.agent.ports import LLMProvider
from app.api.demo_providers import DemoScriptedLLMProvider
from app.config import Settings
from app.llm.evaluation.artifacts import atomic_write_text
from app.llm.evaluation.comparison import compare_reports
from app.llm.evaluation.dataset_loader import load_dataset, load_policy
from app.llm.evaluation.dataset_validator import DatasetValidator, validate_policy
from app.llm.evaluation.errors import (
    ArtifactError,
    ComparisonIncompatibleError,
    DatasetValidationError,
    EvaluationError,
    EvaluationExitCode,
    OnlineGuardError,
    PolicyValidationError,
    ResumeIdentityError,
)
from app.llm.evaluation.gate import evaluate_gate
from app.llm.evaluation.hashing import dataset_hash, sha256_value
from app.llm.evaluation.models import (
    EvaluationComparisonStatus,
    EvaluationDataset,
    EvaluationGatePolicy,
    EvaluationProviderConfiguration,
    EvaluationReport,
)
from app.llm.evaluation.online_guard import require_online_authorization
from app.llm.evaluation.runner import EvaluationRunner, EvaluationRunRequest
from app.llm.factory import build_structured_interpretation_provider
from app.llm.prompts.registry import PromptRegistry
from app.llm.providers.zai_glm import (
    ZaiGLMConfig,
    ZaiGLMStructuredInterpretationProvider,
)
from app.llm.validation.parser import StructuredInterpretationParser

EVAL_ROOT = Path(__file__).resolve().parents[3] / "evals"
DEFAULT_DATASET = EVAL_ROOT / "datasets" / "resident_interpretation_v1.jsonl"
DEFAULT_POLICY = EVAL_ROOT / "policies" / "resident_interpretation_gate_v1.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fixflow-eval")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-dataset")
    validate.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    inspect.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    hash_command = commands.add_parser("hash-dataset")
    hash_command.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    run = commands.add_parser("run")
    run.add_argument("--provider", choices=("scripted", "fake-glm", "glm"), required=True)
    run.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    run.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    run.add_argument("--output", type=Path)
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--fail-fast", action="store_true")
    run.add_argument("--allow-network", action="store_true")
    run.add_argument("--acknowledge-cost", action="store_true")
    run.add_argument("--allow-dirty", action="store_true")
    compare = commands.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    gate = commands.add_parser("gate")
    gate.add_argument("--report", type=Path, required=True)
    gate.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    gate.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate-dataset":
            dataset = _validated_dataset(args.dataset)
            print(
                f"valid dataset={dataset.metadata.dataset_id} "
                f"version={dataset.metadata.dataset_version} cases={len(dataset.cases)} "
                f"hash={dataset.metadata.dataset_hash}"
            )
            return EvaluationExitCode.SUCCESS
        if args.command == "hash-dataset":
            dataset = load_dataset(args.dataset)
            print(dataset_hash(dataset.metadata, dataset.cases))
            return EvaluationExitCode.SUCCESS
        if args.command == "inspect":
            dataset = _validated_dataset(args.dataset)
            policy = load_policy(args.policy)
            validate_policy(policy)
            _print_inspection(dataset, policy)
            return EvaluationExitCode.SUCCESS
        if args.command == "run":
            return asyncio.run(_run(args))
        if args.command == "compare":
            baseline = _load_report(args.baseline)
            candidate = _load_report(args.candidate)
            comparison = compare_reports(baseline, candidate)
            atomic_write_text(args.output, comparison.model_dump_json(indent=2) + "\n")
            if comparison.status is EvaluationComparisonStatus.INCOMPATIBLE:
                return EvaluationExitCode.INCOMPATIBLE
            return EvaluationExitCode.SUCCESS
        if args.command == "gate":
            report = _load_report(args.report)
            policy = load_policy(args.policy)
            validate_policy(policy)
            gate_result = evaluate_gate(report, policy)
            if args.output:
                atomic_write_text(args.output, gate_result.model_dump_json(indent=2) + "\n")
            print(f"gate={'PASSED' if gate_result.overall_passed else 'FAILED'}")
            return (
                EvaluationExitCode.SUCCESS
                if gate_result.overall_passed
                else EvaluationExitCode.GATE_FAILED
            )
    except OnlineGuardError as exc:
        print(str(exc), file=sys.stderr)
        return EvaluationExitCode.ONLINE_GUARD_FAILED
    except (DatasetValidationError, PolicyValidationError, ValidationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EvaluationExitCode.INVALID_INPUT
    except ResumeIdentityError as exc:
        print(str(exc), file=sys.stderr)
        return EvaluationExitCode.INCOMPLETE
    except ComparisonIncompatibleError as exc:
        print(str(exc), file=sys.stderr)
        return EvaluationExitCode.INCOMPATIBLE
    except (ArtifactError, EvaluationError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return EvaluationExitCode.INFRASTRUCTURE_FAILED
    return EvaluationExitCode.INVALID_INPUT


async def _run(args: argparse.Namespace) -> int:
    dataset = _validated_dataset(args.dataset)
    policy = load_policy(args.policy)
    validate_policy(policy)
    prompt = PromptRegistry().resident_interpretation()
    provider, configuration = _provider(args.provider, args, prompt.prompt_hash)
    output = args.output or Path(".artifacts/evaluations") / str(uuid4())
    runner = EvaluationRunner()
    try:
        outcome = await runner.run(
            EvaluationRunRequest(
                dataset=dataset,
                policy=policy,
                provider=provider,
                provider_configuration=configuration,
                output_directory=output,
                concurrency=args.concurrency,
                repeat_count=args.repeats,
                resume=args.resume,
                fail_fast=args.fail_fast,
                allow_dirty=args.allow_dirty,
            )
        )
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()
    print(
        f"run={outcome.report.manifest.run_id} cases={len(outcome.results)} "
        f"gate={_gate_label(outcome.report)} "
        f"artifacts={output}"
    )
    if outcome.report.manifest.run_status.value != "COMPLETED":
        return EvaluationExitCode.INCOMPLETE
    return (
        EvaluationExitCode.SUCCESS
        if outcome.report.gate_result and outcome.report.gate_result.overall_passed
        else EvaluationExitCode.GATE_FAILED
    )


def _provider(
    mode: str,
    args: argparse.Namespace,
    prompt_hash: str,
) -> tuple[LLMProvider, EvaluationProviderConfiguration]:
    prompt = PromptRegistry().resident_interpretation()
    if mode == "scripted":
        scripted: LLMProvider = DemoScriptedLLMProvider()
        return scripted, EvaluationProviderConfiguration(
            provider="fixflow-demo-scripted",
            model="fixflow-demo-v1",
            provider_sdk="built-in",
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.prompt_version,
            prompt_hash=prompt_hash,
            interpretation_schema_version=prompt.schema_version,
            thinking_mode="disabled",
            temperature=0,
            top_p=1,
            max_tokens=1400,
            request_timeout_seconds=1,
            total_timeout_seconds=1,
            max_attempts=1,
            endpoint_fingerprint=sha256_value("offline"),
            live_network=False,
        )
    if mode == "fake-glm":
        fake: LLMProvider = _offline_fake_glm_provider()
        return fake, EvaluationProviderConfiguration(
            provider="zai-fake",
            model="glm-5.1-fake",
            provider_sdk="zai-sdk-fake-transport",
            provider_sdk_version=importlib.metadata.version("zai-sdk"),
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.prompt_version,
            prompt_hash=prompt_hash,
            interpretation_schema_version=prompt.schema_version,
            thinking_mode="disabled",
            temperature=0,
            top_p=1,
            max_tokens=1600,
            request_timeout_seconds=1,
            total_timeout_seconds=2,
            max_attempts=1,
            endpoint_fingerprint=sha256_value("offline-fake-zai-transport"),
            live_network=False,
        )
    require_online_authorization(
        provider=mode,
        allow_network=args.allow_network,
        acknowledge_cost=args.acknowledge_cost,
        api_key_available=_api_key_available(),
    )
    settings = Settings(llm_provider="glm")
    online: LLMProvider = build_structured_interpretation_provider(
        settings,
        scripted_provider=DemoScriptedLLMProvider(),
    )
    return online, EvaluationProviderConfiguration(
        provider="zai",
        model=settings.glm_model,
        provider_sdk="zai-sdk",
        provider_sdk_version=importlib.metadata.version("zai-sdk"),
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.prompt_version,
        prompt_hash=prompt_hash,
        interpretation_schema_version=prompt.schema_version,
        thinking_mode=settings.glm_thinking_mode,
        temperature=settings.glm_temperature,
        top_p=settings.glm_top_p,
        max_tokens=settings.glm_max_tokens,
        request_timeout_seconds=settings.glm_request_timeout_seconds,
        total_timeout_seconds=settings.glm_total_timeout_seconds,
        max_attempts=settings.glm_max_attempts,
        endpoint_fingerprint=_endpoint_fingerprint(settings.glm_base_url),
        live_network=True,
    )


def _offline_fake_glm_provider() -> ZaiGLMStructuredInterpretationProvider:
    """Exercise the production GLM adapter and parser without network or credentials."""

    def completion_create(**_kwargs: object) -> object:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"utterance_intent":"UNKNOWN"}'),
                    finish_reason="stop",
                )
            ],
            request_id="offline-fake-request",
            usage=None,
        )

    return ZaiGLMStructuredInterpretationProvider(
        prompt=PromptRegistry().resident_interpretation(),
        parser=StructuredInterpretationParser(max_response_bytes=65_536),
        config=ZaiGLMConfig(
            model="glm-5.1-fake",
            request_timeout_seconds=1,
            total_timeout_seconds=2,
            max_attempts=1,
            retry_initial_delay_seconds=0,
            retry_max_delay_seconds=0,
            max_concurrency=4,
        ),
        completion_create=completion_create,
    )


def _api_key_available() -> bool:
    try:
        settings = Settings()
    except ValidationError:
        return False
    return settings.glm_api_key is not None and bool(
        settings.glm_api_key.get_secret_value().strip()
    )


def _endpoint_fingerprint(endpoint: str) -> str:
    split = urlsplit(endpoint)
    host = split.hostname or ""
    netloc = f"{host}:{split.port}" if split.port is not None else host
    normalized = urlunsplit((split.scheme.casefold(), netloc, split.path.rstrip("/") + "/", "", ""))
    return sha256_value(normalized)


def _validated_dataset(path: Path) -> EvaluationDataset:
    dataset = load_dataset(path)
    DatasetValidator().validate(
        dataset,
        prompt=PromptRegistry().resident_interpretation(),
    )
    return dataset


def _load_report(path: Path) -> EvaluationReport:
    return EvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))


def _print_inspection(
    dataset: EvaluationDataset,
    policy: EvaluationGatePolicy,
) -> None:
    from collections import Counter

    prompt = PromptRegistry().resident_interpretation()
    print(
        json.dumps(
            {
                "dataset": dataset.metadata.model_dump(mode="json"),
                "suite_distribution": dict(Counter(case.suite.value for case in dataset.cases)),
                "severity_distribution": dict(
                    Counter(case.severity.value for case in dataset.cases)
                ),
                "tag_distribution": dict(
                    Counter(tag for case in dataset.cases for tag in case.tags)
                ),
                "policy": {
                    "policy_id": policy.policy_id,
                    "policy_version": policy.policy_version,
                    "policy_hash": policy.policy_hash,
                },
                "prompt": {
                    "prompt_id": prompt.prompt_id,
                    "prompt_version": prompt.prompt_version,
                    "prompt_hash": prompt.prompt_hash,
                    "schema_version": prompt.schema_version,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def _gate_label(report: EvaluationReport) -> str:
    return "PASSED" if report.gate_result and report.gate_result.overall_passed else "FAILED"


if __name__ == "__main__":
    raise SystemExit(main())
