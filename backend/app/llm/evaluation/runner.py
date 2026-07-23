"""Bounded provider runner with stable ordering, resume, and no business dependencies."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
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
    EvaluationInfrastructureStatus,
    EvaluationProviderConfiguration,
    EvaluationQualityDecision,
    EvaluationReport,
    EvaluationRunManifest,
    EvaluationRunPurpose,
    EvaluationRunStatus,
    EvaluationSchedulerConfiguration,
)
from app.llm.evaluation.scheduler import (
    RETRYABLE_INFRASTRUCTURE_CODES,
    EvaluationScheduler,
)
from app.llm.evaluation.scorer import (
    SCORER_ID,
    score_failure,
)
from app.llm.evaluation.scorer import (
    SCORER_VERSION as SCORER_VERSION_V1,
)
from app.llm.evaluation.scorer import (
    score_success as score_success_v1,
)
from app.llm.evaluation.scorer_v2 import (
    SCORER_VERSION as SCORER_VERSION_V2,
)
from app.llm.evaluation.scorer_v2 import (
    score_success as score_success_v2,
)
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
    prompt_version: str = PromptRegistry.DEFAULT_PROMPT_VERSION
    scorer_version: str = SCORER_VERSION_V1
    run_purpose: EvaluationRunPurpose = EvaluationRunPurpose.REGRESSION
    development_source_fingerprint: str | None = None
    scheduler_configuration: EvaluationSchedulerConfiguration | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.concurrency <= 4:
            raise ValueError("concurrency must be between 1 and 4")
        if not 1 <= self.repeat_count <= 5:
            raise ValueError("repeat_count must be between 1 and 5")
        if self.scorer_version not in {SCORER_VERSION_V1, SCORER_VERSION_V2}:
            raise ValueError(f"unsupported scorer version: {self.scorer_version}")
        if self.run_purpose is EvaluationRunPurpose.PROMPT_DEVELOPMENT:
            if self.scorer_version != SCORER_VERSION_V2:
                raise ValueError("prompt development requires scorer version 2.0.0")
            if self.concurrency != 1:
                raise ValueError("prompt development concurrency must be 1")
            if self.development_source_fingerprint is None:
                raise ValueError("prompt development requires a source fingerprint")


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
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        self._prompts = prompt_registry or PromptRegistry()
        self._clock = clock
        self._monotonic = monotonic
        self._uuid_factory = uuid_factory
        self._sleeper = sleeper
        self._random = random_source or random.Random()
        self._scheduler: EvaluationScheduler | None = None

    async def run(self, request: EvaluationRunRequest) -> EvaluationRunOutcome:
        prompt = self._prompts.resident_interpretation(request.prompt_version)
        configuration = request.provider_configuration
        if not request.resume and (
            configuration.prompt_id != prompt.prompt_id
            or configuration.prompt_version != prompt.prompt_version
            or configuration.prompt_hash != prompt.prompt_hash
            or configuration.interpretation_schema_version != prompt.schema_version
        ):
            raise ValueError("provider configuration does not match selected prompt")
        DatasetValidator().validate(
            request.dataset,
            prompt=prompt,
            require_release_distribution=False,
        )
        validate_policy(request.policy)
        store = EvaluationArtifactStore(request.output_directory)
        store.initialize(resume=request.resume)
        manifest, completed = self._prepare_manifest(request, store)
        scheduler = self._scheduler_for(request.scheduler_configuration)
        results = await self._run_cases(
            request,
            store=store,
            manifest=manifest,
            completed=completed,
            prompt_hash=prompt.prompt_hash,
            schema_version=prompt.schema_version,
            scheduler=scheduler,
        )
        return self._finalize(request, store, manifest, results, scheduler=scheduler)

    def _scheduler_for(
        self,
        configuration: EvaluationSchedulerConfiguration | None,
    ) -> EvaluationScheduler | None:
        if configuration is None:
            return None
        if self._scheduler is None:
            self._scheduler = EvaluationScheduler(
                configuration,
                sleeper=self._sleeper,
                monotonic=self._monotonic,
                random_source=self._random,
            )
        elif self._scheduler.configuration != configuration:
            raise ValueError("one EvaluationRunner cannot mix scheduler configurations")
        return self._scheduler

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
            existing = store.read_valid_results()
            retryable_failures = tuple(
                item
                for item in existing
                if item.status.value == "PROVIDER_FAILED"
                and item.provider_error_code in RETRYABLE_INFRASTRUCTURE_CODES
            )
            store.archive_resume_failures(retryable_failures)
            completed = tuple(item for item in existing if item not in retryable_failures)
            store.write_results(completed)
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
        scheduler: EvaluationScheduler | None,
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
            prompt_version=request.prompt_version,
        )
        try:
            for offset in range(0, len(work), request.concurrency):
                batch = work[offset : offset + request.concurrency]
                batch_results = await asyncio.gather(
                    *(
                        self._scheduled_case(
                            case,
                            repeat_index=repeat_index,
                            node=node,
                            configuration=request.provider_configuration,
                            prompt_hash=prompt_hash,
                            schema_version=schema_version,
                            scorer_version=request.scorer_version,
                            scheduler=scheduler,
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
        *,
        scheduler: EvaluationScheduler | None,
    ) -> EvaluationRunOutcome:
        expected_total = len(request.dataset.cases) * request.repeat_count
        status = (
            EvaluationRunStatus.COMPLETED
            if len(results) == expected_total
            else EvaluationRunStatus.INCOMPLETE
        )
        metrics = calculate_metrics(request.dataset.cases, results)
        infrastructure_blocked = metrics.completion_rate < 1
        final_manifest = manifest.model_copy(
            update={
                "run_status": status,
                "completed_at_utc": self._clock(),
                "scheduler_audit": scheduler.audit() if scheduler is not None else None,
                "infrastructure_status": (
                    EvaluationInfrastructureStatus.EVALUATION_BLOCKED_INFRASTRUCTURE
                    if infrastructure_blocked
                    else EvaluationInfrastructureStatus.READY
                ),
                "quality_decision": (
                    EvaluationQualityDecision.INCONCLUSIVE
                    if infrastructure_blocked
                    else EvaluationQualityDecision.EVALUATED
                ),
            }
        )
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

    async def _scheduled_case(
        self,
        case: EvaluationCase,
        *,
        repeat_index: int,
        node: InterpretMessageNode,
        configuration: EvaluationProviderConfiguration,
        prompt_hash: str,
        schema_version: str,
        scorer_version: str,
        scheduler: EvaluationScheduler | None,
    ) -> EvaluationCaseResult:
        async def attempt() -> EvaluationCaseResult:
            return await self._evaluate_case(
                case,
                repeat_index=repeat_index,
                node=node,
                configuration=configuration,
                prompt_hash=prompt_hash,
                schema_version=schema_version,
                scorer_version=scorer_version,
            )

        if scheduler is None:
            return await attempt()
        return await scheduler.evaluate(
            case_id=case.case_id,
            repeat_index=repeat_index,
            attempt=attempt,
        )

    async def _evaluate_case(
        self,
        case: EvaluationCase,
        *,
        repeat_index: int,
        node: InterpretMessageNode,
        configuration: EvaluationProviderConfiguration,
        prompt_hash: str,
        schema_version: str,
        scorer_version: str,
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
                provider_retry_after_seconds=(
                    provider_error.retry_after_seconds if provider_error else None
                ),
            )
        completed_at = self._clock()
        scorer = score_success_v2 if scorer_version == SCORER_VERSION_V2 else score_success_v1
        return scorer(
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
            scorer_version=request.scorer_version,
            provider_configuration=request.provider_configuration,
            concurrency=request.concurrency,
            repeat_count=request.repeat_count,
            code_commit=git.commit,
            git_dirty=git.dirty,
            settings_fingerprint=settings_fingerprint(request.provider_configuration),
            created_at_utc=self._clock(),
            case_count=len(request.dataset.cases),
            run_purpose=request.run_purpose,
            baseline_eligible=False,
            qualification_eligible=False,
            release_candidate_eligible=False,
            development_source_fingerprint=request.development_source_fingerprint,
            scheduler_configuration=request.scheduler_configuration,
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
            "code_commit",
            "run_purpose",
            "development_source_fingerprint",
            "scheduler_configuration",
        )
        for field in identity_fields:
            if getattr(previous, field) != getattr(candidate, field):
                raise ResumeIdentityError(f"resume identity mismatch: {field}")


def _case_order(cases: tuple[EvaluationCase, ...], case_id: str) -> int:
    return next(index for index, case in enumerate(cases) if case.case_id == case_id)
