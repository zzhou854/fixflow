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
from app.llm.evaluation.challenge import (
    reject_challenge_for_prompt_development,
    validate_challenge_isolation,
)
from app.llm.evaluation.comparison import compare_reports
from app.llm.evaluation.dataset_loader import load_dataset, load_policy
from app.llm.evaluation.dataset_validator import DatasetValidator, validate_policy
from app.llm.evaluation.development import (
    DevelopmentRunSummary,
    DevelopmentStatus,
    combine_repeat_results,
    critical_failure_counts,
    evaluate_development_gate,
    evaluate_smoke_gate,
    evaluate_stability_gate,
)
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
from app.llm.evaluation.manifest import (
    development_source_fingerprint,
    read_git_identity,
    source_tree_fingerprint,
)
from app.llm.evaluation.metrics import calculate_metrics
from app.llm.evaluation.models import (
    EvaluationCaseResult,
    EvaluationComparisonStatus,
    EvaluationDataset,
    EvaluationGatePolicy,
    EvaluationProviderConfiguration,
    EvaluationReport,
    EvaluationRunPurpose,
    EvaluationSchedulerConfiguration,
)
from app.llm.evaluation.online_guard import require_online_authorization
from app.llm.evaluation.runner import EvaluationRunner, EvaluationRunRequest
from app.llm.factory import build_structured_interpretation_provider
from app.llm.prompts.registry import PromptDefinition, PromptRegistry
from app.llm.providers.zai_glm import (
    ZaiGLMConfig,
    ZaiGLMStructuredInterpretationProvider,
)
from app.llm.validation.parser import StructuredInterpretationParser

EVAL_ROOT = Path(__file__).resolve().parents[3] / "evals"
DEFAULT_DATASET = EVAL_ROOT / "datasets" / "resident_interpretation_v1.jsonl"
DEFAULT_POLICY = EVAL_ROOT / "policies" / "resident_interpretation_gate_v1.json"
CHALLENGE_DATASET = EVAL_ROOT / "datasets" / "resident_interpretation_challenge_v1.jsonl"
DEVELOPMENT_SMOKE = EVAL_ROOT / "development" / "resident_interpretation_prompt_v2_smoke.json"


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
    run.add_argument(
        "--prompt-version",
        choices=PromptRegistry.SUPPORTED_VERSIONS,
        default=PromptRegistry.DEFAULT_PROMPT_VERSION,
    )
    run.add_argument(
        "--run-purpose",
        choices=("regression", "prompt-development"),
        default="regression",
    )
    run.add_argument("--development-source-fingerprint")
    _add_scheduler_arguments(run)
    develop = commands.add_parser("develop-prompt-v2")
    develop.add_argument("--allow-network", action="store_true")
    develop.add_argument("--acknowledge-cost", action="store_true")
    develop.add_argument(
        "--run-purpose",
        choices=("prompt-development",),
        required=True,
    )
    develop.add_argument("--output", type=Path, required=True)
    _add_scheduler_arguments(develop)
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
        if args.command == "develop-prompt-v2":
            return asyncio.run(_develop_prompt_v2(args))
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


def _add_scheduler_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--requests-per-minute", type=int, default=20)
    parser.add_argument("--minimum-request-interval-seconds", type=float, default=3.0)
    parser.add_argument("--retry-after-seconds", type=float)
    parser.add_argument("--initial-backoff-seconds", type=float, default=5.0)
    parser.add_argument("--maximum-backoff-seconds", type=float, default=60.0)
    parser.add_argument("--jitter-seconds", type=float, default=1.0)
    parser.add_argument("--maximum-provider-attempts", type=int, default=5)
    parser.add_argument("--consecutive-rate-limit-threshold", type=int, default=2)
    parser.add_argument("--circuit-pause-seconds", type=float, default=60.0)
    parser.add_argument("--maximum-circuit-pauses", type=int, default=3)


def _scheduler_configuration(args: argparse.Namespace) -> EvaluationSchedulerConfiguration:
    return EvaluationSchedulerConfiguration(
        requests_per_minute=args.requests_per_minute,
        minimum_request_interval_seconds=args.minimum_request_interval_seconds,
        retry_after_seconds=args.retry_after_seconds,
        initial_backoff_seconds=args.initial_backoff_seconds,
        maximum_backoff_seconds=args.maximum_backoff_seconds,
        jitter_seconds=args.jitter_seconds,
        maximum_provider_attempts=args.maximum_provider_attempts,
        consecutive_rate_limit_threshold=args.consecutive_rate_limit_threshold,
        circuit_pause_seconds=args.circuit_pause_seconds,
        maximum_circuit_pauses=args.maximum_circuit_pauses,
    )


async def _develop_prompt_v2(args: argparse.Namespace) -> int:
    require_online_authorization(
        provider="glm",
        allow_network=args.allow_network,
        acknowledge_cost=args.acknowledge_cost,
        api_key_available=_api_key_available(),
    )
    registry = PromptRegistry()
    prompt_v1 = registry.resident_interpretation("1.0.0")
    prompt_v2 = registry.resident_interpretation("2.0.0")
    regression = _validated_dataset(DEFAULT_DATASET, prompt_version="2.0.0")
    challenge = _validated_dataset(CHALLENGE_DATASET, prompt_version="2.0.0")
    validate_challenge_isolation(
        challenge,
        regression=regression,
        prompts=(prompt_v1, prompt_v2),
    )
    reject_challenge_for_prompt_development(
        challenge.metadata.dataset_id,
        live_network=False,
    )
    policy = load_policy(DEFAULT_POLICY)
    validate_policy(policy)
    fingerprint = _development_fingerprint(
        prompt_hash=prompt_v2.prompt_hash,
        challenge_hash=challenge.metadata.dataset_hash,
        policy_hash=policy.policy_hash,
    )
    provider_args = argparse.Namespace(allow_network=True, acknowledge_cost=True)
    provider, configuration = _provider("glm", provider_args, prompt_v2)
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    all_results: tuple[EvaluationCaseResult, ...] = ()
    probe_results: tuple[EvaluationCaseResult, ...] = ()
    smoke_gate = None
    full_gate = None
    stability = None
    stability_gate = None
    status = DevelopmentStatus.NOT_READY
    runner = EvaluationRunner()
    try:
        probe = _probe_dataset(regression)
        probe_outcome = await runner.run(
            _development_request(
                dataset=probe,
                policy=policy,
                provider=provider,
                configuration=configuration,
                output=output / "raw-probe",
                fingerprint=fingerprint,
                scheduler_configuration=_scheduler_configuration(args),
                fail_fast=True,
            )
        )
        probe_results = probe_outcome.results
        if probe_outcome.report.metrics.completion_rate < 1:
            status = DevelopmentStatus.EVALUATION_BLOCKED_INFRASTRUCTURE
            raise _DevelopmentStop
        smoke = _smoke_dataset(regression)
        smoke_outcome = await runner.run(
            _development_request(
                dataset=smoke,
                policy=policy,
                provider=provider,
                configuration=configuration,
                output=output / "raw-smoke",
                fingerprint=fingerprint,
                scheduler_configuration=_scheduler_configuration(args),
            )
        )
        all_results = smoke_outcome.results
        smoke_gate = evaluate_smoke_gate(smoke_outcome.report.metrics)
        if smoke_gate.passed:
            first = await runner.run(
                _development_request(
                    dataset=regression,
                    policy=policy,
                    provider=provider,
                    configuration=configuration,
                    output=output / "raw-repeat-1",
                    fingerprint=fingerprint,
                    scheduler_configuration=_scheduler_configuration(args),
                )
            )
            all_results = (*all_results, *first.results)
            full_gate = evaluate_development_gate(first.report.metrics)
            if full_gate.passed:
                second = await runner.run(
                    _development_request(
                        dataset=regression,
                        policy=policy,
                        provider=provider,
                        configuration=configuration,
                        output=output / "raw-repeat-2",
                        fingerprint=fingerprint,
                        scheduler_configuration=_scheduler_configuration(args),
                    )
                )
                all_results = (*all_results, *second.results)
                repeated = combine_repeat_results(first.results, second.results)
                combined_metrics = calculate_metrics(regression.cases, repeated)
                stability, stability_gate = evaluate_stability_gate(repeated)
                full_gate = evaluate_development_gate(combined_metrics)
                if full_gate.passed and stability_gate.passed:
                    status = DevelopmentStatus.READY_FOR_REQUALIFICATION
    except _DevelopmentStop:
        pass
    finally:
        await provider.close()  # type: ignore[attr-defined]

    quality_results = all_results
    stage_outcomes = tuple(
        item
        for item in (
            locals().get("probe_outcome"),
            locals().get("smoke_outcome"),
            locals().get("first"),
            locals().get("second"),
        )
        if item is not None
    )
    final_audit = stage_outcomes[-1].report.manifest.scheduler_audit if stage_outcomes else None
    summary = DevelopmentRunSummary(
        status=status,
        prompt_id=prompt_v2.prompt_id,
        prompt_version=prompt_v2.prompt_version,
        prompt_hash=prompt_v2.prompt_hash,
        dataset_id=regression.metadata.dataset_id,
        dataset_version=regression.metadata.dataset_version,
        dataset_hash=regression.metadata.dataset_hash,
        scorer_id="resident_interpretation_scorer",
        scorer_version="2.0.0",
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_hash=policy.policy_hash,
        development_source_fingerprint=fingerprint,
        probe_case_count=len(probe_results),
        smoke_case_count=24 if "smoke_outcome" in locals() else 0,
        development_call_count=len(quality_results),
        upstream_attempt_count=(
            final_audit.upstream_attempt_count if final_audit is not None else 0
        ),
        provider_failure_count=sum(
            item.provider_error_code is not None for item in (*probe_results, *quality_results)
        ),
        retry_case_count=final_audit.retry_count if final_audit is not None else 0,
        rate_limit_wait_seconds=(final_audit.total_wait_seconds if final_audit is not None else 0),
        circuit_pause_count=(final_audit.circuit_pause_count if final_audit is not None else 0),
        failed_case_ids=tuple(
            sorted(
                {
                    item.case_id
                    for item in (*probe_results, *quality_results)
                    if not item.case_passed
                }
            )
        ),
        critical_failure_counts=critical_failure_counts(quality_results),
        metrics=(
            calculate_metrics(
                regression.cases,
                combine_repeat_results(first.results, second.results),
            )
            if "first" in locals() and "second" in locals()
            else first.report.metrics
            if "first" in locals()
            else smoke_outcome.report.metrics
            if "smoke_outcome" in locals()
            else probe_outcome.report.metrics
        ),
    )
    atomic_write_text(
        output / "development-run-summary.json",
        summary.model_dump_json(indent=2) + "\n",
    )
    atomic_write_text(
        output / "development-gate-result.json",
        json.dumps(
            {
                "smoke": smoke_gate.model_dump(mode="json") if smoke_gate else None,
                "absolute": full_gate.model_dump(mode="json") if full_gate else None,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
    )
    atomic_write_text(
        output / "development-stability-result.json",
        (
            stability.model_dump_json(indent=2)
            if stability is not None
            else json.dumps(
                {
                    "gate_passed": False,
                    "not_evaluated": True,
                    "reason": "prior development gate failed",
                },
                sort_keys=True,
                indent=2,
            )
        )
        + "\n",
    )
    print(
        f"development_status={status.value} calls={len(all_results)} "
        f"challenge_calls=0 artifacts={output}"
    )
    if status is DevelopmentStatus.EVALUATION_BLOCKED_INFRASTRUCTURE:
        return EvaluationExitCode.INFRASTRUCTURE_FAILED
    if status is DevelopmentStatus.READY_FOR_REQUALIFICATION:
        return EvaluationExitCode.SUCCESS
    return EvaluationExitCode.GATE_FAILED


def _development_request(
    *,
    dataset: EvaluationDataset,
    policy: EvaluationGatePolicy,
    provider: LLMProvider,
    configuration: EvaluationProviderConfiguration,
    output: Path,
    fingerprint: str,
    scheduler_configuration: EvaluationSchedulerConfiguration,
    fail_fast: bool = False,
) -> EvaluationRunRequest:
    return EvaluationRunRequest(
        dataset=dataset,
        policy=policy,
        provider=provider,
        provider_configuration=configuration,
        output_directory=output,
        concurrency=1,
        repeat_count=1,
        allow_dirty=True,
        prompt_version="2.0.0",
        scorer_version="2.0.0",
        run_purpose=EvaluationRunPurpose.PROMPT_DEVELOPMENT,
        development_source_fingerprint=fingerprint,
        scheduler_configuration=scheduler_configuration,
        fail_fast=fail_fast,
    )


def _smoke_dataset(dataset: EvaluationDataset) -> EvaluationDataset:
    raw = json.loads(DEVELOPMENT_SMOKE.read_text(encoding="utf-8"))
    ids = tuple(raw["case_ids"])
    if len(ids) != 24 or len(set(ids)) != 24:
        raise ValueError("development smoke must contain 24 unique case ids")
    by_id = {case.case_id: case for case in dataset.cases}
    try:
        cases = tuple(by_id[case_id] for case_id in ids)
    except KeyError as exc:
        raise ValueError("development smoke references an unknown case") from exc
    metadata = dataset.metadata.model_copy(update={"case_count": len(cases)})
    metadata = metadata.model_copy(update={"dataset_hash": dataset_hash(metadata, cases)})
    return dataset.model_copy(update={"metadata": metadata, "cases": cases})


def _probe_dataset(dataset: EvaluationDataset) -> EvaluationDataset:
    smoke = _smoke_dataset(dataset)
    cases = smoke.cases[:8]
    metadata = dataset.metadata.model_copy(update={"case_count": len(cases)})
    metadata = metadata.model_copy(update={"dataset_hash": dataset_hash(metadata, cases)})
    return dataset.model_copy(update={"metadata": metadata, "cases": cases})


class _DevelopmentStop(Exception):
    """Internal non-error control flow for a blocked development stage."""


def _development_fingerprint(
    *,
    prompt_hash: str,
    challenge_hash: str,
    policy_hash: str,
) -> str:
    git = read_git_identity()
    if git.commit is None:
        raise ValueError("Git HEAD is required for development source identity")
    paths = (
        Path("backend/app/agent/nodes/interpret_message.py"),
        Path("backend/app/llm/prompts/registry.py"),
        Path("backend/app/llm/prompts/resident_interpretation/system_v2.md"),
        Path("backend/app/llm/prompts/resident_interpretation/examples_v2.json"),
        Path("backend/app/llm/prompts/resident_interpretation/output_schema_v1.json"),
        Path("backend/app/llm/evaluation/scorer_v2.py"),
        Path("backend/app/llm/evaluation/development.py"),
        Path("backend/evals/datasets/resident_interpretation_challenge_v1.jsonl"),
        Path("backend/evals/datasets/resident_interpretation_challenge_v1.manifest.json"),
        DEVELOPMENT_SMOKE,
        DEFAULT_POLICY,
    )
    diff_hash = source_tree_fingerprint(paths)
    return development_source_fingerprint(
        head_commit=git.commit,
        prompt_hash=prompt_hash,
        challenge_dataset_hash=challenge_hash,
        scorer_identity="resident_interpretation_scorer@2.0.0",
        policy_identity=f"resident_interpretation_gate@1.0.0:{policy_hash}",
        git_diff_hash=diff_hash,
    )


async def _run(args: argparse.Namespace) -> int:
    prompt = PromptRegistry().resident_interpretation(args.prompt_version)
    dataset = _validated_dataset(args.dataset, prompt_version=args.prompt_version)
    policy = load_policy(args.policy)
    validate_policy(policy)
    development = args.run_purpose == "prompt-development"
    if development:
        if args.prompt_version != "2.0.0":
            raise ValueError("prompt-development requires --prompt-version 2.0.0")
        if args.concurrency != 1:
            raise ValueError("prompt-development requires --concurrency 1")
        if not args.development_source_fingerprint:
            raise ValueError("prompt-development requires --development-source-fingerprint")
        reject_challenge_for_prompt_development(
            dataset.metadata.dataset_id,
            live_network=args.provider == "glm",
        )
    provider, configuration = _provider(args.provider, args, prompt)
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
                prompt_version=prompt.prompt_version,
                scorer_version="2.0.0" if development else "1.0.0",
                run_purpose=(
                    EvaluationRunPurpose.PROMPT_DEVELOPMENT
                    if development
                    else EvaluationRunPurpose.REGRESSION
                ),
                development_source_fingerprint=args.development_source_fingerprint,
                scheduler_configuration=(
                    _scheduler_configuration(args)
                    if args.provider == "glm" and args.run_purpose in {"prompt-development"}
                    else None
                ),
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
    prompt: PromptDefinition,
) -> tuple[LLMProvider, EvaluationProviderConfiguration]:
    if mode == "scripted":
        scripted: LLMProvider = DemoScriptedLLMProvider()
        return scripted, EvaluationProviderConfiguration(
            provider="fixflow-demo-scripted",
            model="fixflow-demo-v1",
            provider_sdk="built-in",
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.prompt_version,
            prompt_hash=prompt.prompt_hash,
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
        fake: LLMProvider = _offline_fake_glm_provider(prompt)
        return fake, EvaluationProviderConfiguration(
            provider="zai-fake",
            model="glm-5.1-fake",
            provider_sdk="zai-sdk-fake-transport",
            provider_sdk_version=importlib.metadata.version("zai-sdk"),
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.prompt_version,
            prompt_hash=prompt.prompt_hash,
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
        prompt_version=prompt.prompt_version,
        provider_max_attempts=1,
        provider_max_concurrency=1,
    )
    return online, EvaluationProviderConfiguration(
        provider="zai",
        model=settings.glm_model,
        provider_sdk="zai-sdk",
        provider_sdk_version=importlib.metadata.version("zai-sdk"),
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.prompt_version,
        prompt_hash=prompt.prompt_hash,
        interpretation_schema_version=prompt.schema_version,
        thinking_mode=settings.glm_thinking_mode,
        temperature=settings.glm_temperature,
        top_p=settings.glm_top_p,
        max_tokens=settings.glm_max_tokens,
        request_timeout_seconds=settings.glm_request_timeout_seconds,
        total_timeout_seconds=settings.glm_total_timeout_seconds,
        max_attempts=1,
        endpoint_fingerprint=_endpoint_fingerprint(settings.glm_base_url),
        live_network=True,
    )


def _offline_fake_glm_provider(
    prompt: PromptDefinition | None = None,
) -> ZaiGLMStructuredInterpretationProvider:
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
        prompt=prompt or PromptRegistry().resident_interpretation(),
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


def _validated_dataset(
    path: Path,
    *,
    prompt_version: str | None = None,
) -> EvaluationDataset:
    dataset = load_dataset(path)
    DatasetValidator().validate(
        dataset,
        prompt=PromptRegistry().resident_interpretation(prompt_version),
        require_release_distribution=(dataset.metadata.dataset_id == "resident_interpretation"),
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
