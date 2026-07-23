"""Bounded provider runner with stable ordering, resume, and no business dependencies."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.agent.errors import AgentError, StructuredOutputInvalid
from app.agent.nodes.interpret_message import InterpretMessageNode
from app.agent.ports import LLMProvider
from app.llm.errors import LLMProviderError
from app.llm.evaluation.artifacts import EvaluationArtifactStore
from app.llm.evaluation.dataset_validator import DatasetValidator, validate_policy
from app.llm.evaluation.errors import ArtifactError, ResumeIdentityError
from app.llm.evaluation.gate import evaluate_gate
from app.llm.evaluation.manifest import read_git_identity, settings_fingerprint
from app.llm.evaluation.metrics import calculate_metrics
from app.llm.evaluation.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationGatePolicy,
    EvaluationProviderConfiguration,
    EvaluationReport,
    EvaluationRunManifest,
    EvaluationRunStatus,
)
from app.llm.evaluation.scorer import SCORER_ID, SCORER_VERSION, score_failure, score_success
from app.llm.prompts.registry import PromptRegistry


@dataclass(frozen=True, slots=True)
class EvaluationRunRequest:
    dataset: EvaluationDataset
    policy: EvaluationGatePolicy
    provider: LLMProvider
    provider_configuration: EvaluationProviderConfiguration
    output_directory: Path
    concurrency: int = 1
    repeat_count: int = 1
    resume: bool = False
    fail_fast: bool = False
    allow_dirty: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.concurrency <= 4:
            raise ValueError("concurrency must be between 1 and 4")
        if not 1 <= self.repeat_count <= 5:
            raise ValueError("repeat_count must be between 1 and 5")


@dataclass(frozen=True, slots=True)
class EvaluationRunOutcome:
    report: EvaluationReport
    results: tuple[EvaluationCaseResult, ...]


class EvaluationRunner:
    def __init__(
        self,
        *,
        prompt_registry: PromptRegistry | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._prompts = prompt_registry or PromptRegistry()
        self._clock = clock
        self._monotonic = monotonic
        self._uuid_factory = uuid_factory

    async def run(self, request: EvaluationRunRequest) -> EvaluationRunOutcome:
        prompt = self._prompts.resident_interpretation()
        DatasetValidator().validate(
            request.dataset,
            prompt=prompt,
            require_release_distribution=False,
        )
        validate_policy(request.policy)
        store = EvaluationArtifactStore(request.output_directory)
        store.initialize(resume=request.resume)
        manifest, completed = self._prepare_manifest(request, store)
        results = await self._run_cases(
            request,
            store=store,
            manifest=manifest,
            completed=completed,
            prompt_hash=prompt.prompt_hash,
            schema_version=prompt.schema_version,
        )
        return self._finalize(request, store, manifest, results)

    def _prepare_manifest(
        self,
        request: EvaluationRunRequest,
        store: EvaluationArtifactStore,
    ) -> tuple[EvaluationRunManifest, tuple[EvaluationCaseResult, ...]]:
        manifest = self._manifest(request)
        completed: tuple[EvaluationCaseResult, ...] = ()
        if request.resume:
            previous = store.read_manifest()
            self._validate_resume(previous, manifest)
            manifest = previous.model_copy(
                update={
                    "run_status": EvaluationRunStatus.RUNNING,
                    "completed_at_utc": None,
                    "concurrency": request.concurrency,
                }
            )
            completed = store.read_valid_results()
        store.write_manifest(manifest)
        return manifest, completed

    async def _run_cases(
        self,
        request: EvaluationRunRequest,
        *,
        store: EvaluationArtifactStore,
        manifest: EvaluationRunManifest,
        completed: tuple[EvaluationCaseResult, ...],
        prompt_hash: str,
        schema_version: str,
    ) -> tuple[EvaluationCaseResult, ...]:
        results = list(completed)
        done = {(item.case_id, item.repeat_index) for item in completed}
        work = [
            (case, repeat_index)
            for case in request.dataset.cases
            for repeat_index in range(request.repeat_count)
            if (case.case_id, repeat_index) not in done
        ]
        node = InterpretMessageNode(
            request.provider,
            model=request.provider_configuration.model,
            prompt_registry=self._prompts,
        )
        try:
            for offset in range(0, len(work), request.concurrency):
                batch = work[offset : offset + request.concurrency]
                batch_results = await asyncio.gather(
                    *(
                        self._evaluate_case(
                            case,
                            repeat_index=repeat_index,
                            node=node,
                            configuration=request.provider_configuration,
                            prompt_hash=prompt_hash,
                            schema_version=schema_version,
                        )
                        for case, repeat_index in batch
                    )
                )
                results.extend(batch_results)
                results.sort(
                    key=lambda item: (
                        _case_order(request.dataset.cases, item.case_id),
                        item.repeat_index,
                    )
                )
                store.write_results(tuple(results))
                if request.fail_fast and any(not item.case_passed for item in batch_results):
                    break
        except (asyncio.CancelledError, KeyboardInterrupt):
            incomplete = manifest.model_copy(
                update={
                    "run_status": EvaluationRunStatus.INCOMPLETE,
                    "completed_at_utc": self._clock(),
                }
            )
            store.write_manifest(incomplete)
            raise
        except ArtifactError:
            failed = manifest.model_copy(
                update={
                    "run_status": EvaluationRunStatus.INCOMPLETE,
                    "completed_at_utc": self._clock(),
                }
            )
            try:
                store.write_manifest(failed)
            except ArtifactError:
                pass
            raise
        except Exception:
            failed = manifest.model_copy(
                update={
                    "run_status": EvaluationRunStatus.FAILED,
                    "completed_at_utc": self._clock(),
                }
            )
            store.write_manifest(failed)
            raise
        return tuple(results)

    def _finalize(
        self,
        request: EvaluationRunRequest,
        store: EvaluationArtifactStore,
        manifest: EvaluationRunManifest,
        results: tuple[EvaluationCaseResult, ...],
    ) -> EvaluationRunOutcome:
        expected_total = len(request.dataset.cases) * request.repeat_count
        status = (
            EvaluationRunStatus.COMPLETED
            if len(results) == expected_total
            else EvaluationRunStatus.INCOMPLETE
        )
        final_manifest = manifest.model_copy(
            update={"run_status": status, "completed_at_utc": self._clock()}
        )
        metrics = calculate_metrics(request.dataset.cases, results)
        preliminary = EvaluationReport(
            report_schema_version="evaluation-report-v1",
            manifest=final_manifest,
            metrics=metrics,
        )
        gate = evaluate_gate(
            preliminary,
            request.policy,
            results=results,
            clock=self._clock,
        )
        report = preliminary.model_copy(update={"gate_result": gate})
        store.write_manifest(final_manifest)
        store.finalize(report, results, gate)
        return EvaluationRunOutcome(report=report, results=results)

    async def _evaluate_case(
        self,
        case: EvaluationCase,
        *,
        repeat_index: int,
        node: InterpretMessageNode,
        configuration: EvaluationProviderConfiguration,
        prompt_hash: str,
        schema_version: str,
    ) -> EvaluationCaseResult:
        started_at, started = self._clock(), self._monotonic()
        try:
            result = await node(case.input.to_provider_input())
        except asyncio.CancelledError:
            raise
        except AgentError as exc:
            completed_at = self._clock()
            cause = exc.__cause__
            provider_error = cause if isinstance(cause, LLMProviderError) else None
            return score_failure(
                case,
                repeat_index=repeat_index,
                provider=configuration.provider,
                model=configuration.model,
                prompt_id=configuration.prompt_id,
                prompt_version=configuration.prompt_version,
                prompt_hash=prompt_hash,
                schema_version=schema_version,
                started_at=started_at,
                completed_at=completed_at,
                latency_ms=max(0, int((self._monotonic() - started) * 1000)),
                error_code=provider_error.code.value if provider_error else exc.code,
                invalid_output=isinstance(exc, StructuredOutputInvalid),
                attempt_count=provider_error.attempt_count if provider_error else 1,
            )
        completed_at = self._clock()
        return score_success(
            case,
            repeat_index=repeat_index,
            result=result,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=max(0, int((self._monotonic() - started) * 1000)),
        )

    def _manifest(self, request: EvaluationRunRequest) -> EvaluationRunManifest:
        git = read_git_identity()
        if (
            request.provider_configuration.live_network
            and git.dirty is True
            and not request.allow_dirty
        ):
            raise ValueError("online evaluation requires a clean Git worktree or --allow-dirty")
        metadata = request.dataset.metadata
        return EvaluationRunManifest(
            run_id=self._uuid_factory(),
            run_schema_version="evaluation-run-v1",
            run_status=EvaluationRunStatus.RUNNING,
            dataset_id=metadata.dataset_id,
            dataset_version=metadata.dataset_version,
            dataset_hash=metadata.dataset_hash,
            policy_id=request.policy.policy_id,
            policy_version=request.policy.policy_version,
            policy_hash=request.policy.policy_hash,
            scorer_id=SCORER_ID,
            scorer_version=SCORER_VERSION,
            provider_configuration=request.provider_configuration,
            concurrency=request.concurrency,
            repeat_count=request.repeat_count,
            code_commit=git.commit,
            git_dirty=git.dirty,
            settings_fingerprint=settings_fingerprint(request.provider_configuration),
            created_at_utc=self._clock(),
            case_count=len(request.dataset.cases),
        )

    @staticmethod
    def _validate_resume(
        previous: EvaluationRunManifest,
        candidate: EvaluationRunManifest,
    ) -> None:
        identity_fields = (
            "dataset_id",
            "dataset_version",
            "dataset_hash",
            "policy_id",
            "policy_version",
            "policy_hash",
            "scorer_version",
            "provider_configuration",
            "settings_fingerprint",
            "repeat_count",
        )
        for field in identity_fields:
            if getattr(previous, field) != getattr(candidate, field):
                raise ResumeIdentityError(f"resume identity mismatch: {field}")


def _case_order(cases: tuple[EvaluationCase, ...], case_id: str) -> int:
    return next(index for index, case in enumerate(cases) if case.case_id == case_id)
