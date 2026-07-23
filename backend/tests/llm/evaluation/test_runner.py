import asyncio
import hashlib
import json
import socket
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.agent.models import (
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from app.llm.errors import LLMProviderError, LLMProviderErrorCode
from app.llm.evaluation.artifacts import ARTIFACT_FILES, EvaluationArtifactStore
from app.llm.evaluation.errors import (
    ArtifactError,
    DatasetValidationError,
    ResumeIdentityError,
)
from app.llm.evaluation.hashing import dataset_hash
from app.llm.evaluation.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationDatasetMetadata,
    EvaluationProviderConfiguration,
)
from app.llm.evaluation.runner import (
    EvaluationRunner,
    EvaluationRunRequest,
)
from app.llm.evaluation.scorer import score_failure
from app.llm.prompts.registry import PromptRegistry
from app.llm.providers.zai_glm import (
    ZaiGLMConfig,
    ZaiGLMStructuredInterpretationProvider,
)
from app.llm.validation.parser import StructuredInterpretationParser
from pydantic import BaseModel
from tests.fakes.llm import ScriptedLLMProvider
from tests.llm.evaluation.helpers import provider_configuration


def _dataset(
    release_dataset: EvaluationDataset,
    *,
    count: int = 1,
) -> EvaluationDataset:
    return _dataset_with_cases(release_dataset, release_dataset.cases[:count])


def _dataset_with_cases(
    release_dataset: EvaluationDataset,
    cases: tuple[EvaluationCase, ...],
) -> EvaluationDataset:
    metadata = EvaluationDatasetMetadata(
        **{
            **release_dataset.metadata.model_dump(mode="python"),
            "case_count": len(cases),
            "dataset_hash": "0" * 64,
        }
    )
    metadata = metadata.model_copy(update={"dataset_hash": dataset_hash(metadata, cases)})
    return EvaluationDataset(metadata=metadata, cases=cases)


def _payload(case: object) -> dict[str, object]:
    from app.llm.evaluation.models import EvaluationCase

    assert isinstance(case, EvaluationCase)
    values: dict[str, object] = {}
    for expected in case.expected.fields:
        key = expected.path.removeprefix("$.")
        if expected.matcher.value in {"EXACT", "BOOLEAN"}:
            values[key] = expected.value
        elif expected.matcher.value == "SET_EXACT":
            values[key] = list(expected.value) if isinstance(expected.value, tuple) else []
    values.setdefault("utterance_intent", "UNKNOWN")
    return values


@pytest.mark.asyncio
async def test_scripted_offline_vertical_writes_all_artifacts(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _dataset(release_dataset)
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")),
    )
    provider = ScriptedLLMProvider(structured=[_payload(dataset.cases[0])])
    outcome = await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=provider,
            provider_configuration=provider_configuration(),
            output_directory=tmp_path / "run",
        )
    )
    assert outcome.report.manifest.run_status.value == "COMPLETED"
    assert outcome.results[0].case_passed is True
    assert set(ARTIFACT_FILES) == {path.name for path in (tmp_path / "run").iterdir()}
    index = json.loads((tmp_path / "run" / "artifact-index.json").read_text(encoding="utf-8"))
    for name, expected_hash in index["file_hashes"].items():
        content = (tmp_path / "run" / name).read_text(encoding="utf-8")
        assert hashlib.sha256(content.encode("utf-8")).hexdigest() == expected_hash


@pytest.mark.asyncio
async def test_runner_rejects_invalid_dataset_before_provider_or_artifact(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    dataset = _dataset(release_dataset).model_copy(
        update={
            "metadata": _dataset(release_dataset).metadata.model_copy(
                update={"dataset_hash": "f" * 64}
            )
        }
    )
    provider = ScriptedLLMProvider(structured=[])
    with pytest.raises(DatasetValidationError, match="dataset_hash"):
        await EvaluationRunner().run(
            EvaluationRunRequest(
                dataset=dataset,
                policy=gate_policy,  # type: ignore[arg-type]
                provider=provider,
                provider_configuration=provider_configuration(),
                output_directory=tmp_path / "must-not-exist",
            )
        )
    assert provider.structured_calls == []
    assert not (tmp_path / "must-not-exist").exists()


def _fake_response(payload: dict[str, object]) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)),
                finish_reason="stop",
            )
        ],
        request_id="fake-request",
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
    )


def _fake_glm(
    payloads: list[dict[str, object] | str | Exception],
) -> ZaiGLMStructuredInterpretationProvider:
    queue = list(payloads)

    def create(**_kwargs: object) -> object:
        value = queue.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=value),
                        finish_reason="stop",
                    )
                ],
                request_id="fake-request",
                usage=None,
            )
        return _fake_response(value)

    return ZaiGLMStructuredInterpretationProvider(
        prompt=PromptRegistry().resident_interpretation(),
        parser=StructuredInterpretationParser(max_response_bytes=65_536),
        config=ZaiGLMConfig(
            request_timeout_seconds=1,
            total_timeout_seconds=2,
            max_attempts=1,
            retry_initial_delay_seconds=0,
            retry_max_delay_seconds=0,
        ),
        completion_create=create,
    )


def _zai_config() -> EvaluationProviderConfiguration:
    return provider_configuration(provider="zai", model="glm-5.1")


@pytest.mark.asyncio
async def test_fake_glm_uses_real_adapter_parser_and_gate(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    dataset = _dataset(release_dataset)
    outcome = await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=_fake_glm([_payload(dataset.cases[0])]),
            provider_configuration=_zai_config(),
            output_directory=tmp_path / "fake-glm",
        )
    )
    assert outcome.results[0].provider == "zai"
    assert outcome.results[0].case_passed is True
    assert outcome.results[0].usage.total_tokens == 6


@pytest.mark.asyncio
async def test_fake_glm_safety_miss_fails_critical_gate(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    safety = next(case for case in release_dataset.cases if case.suite.value == "SAFETY")
    dataset = _dataset_with_cases(release_dataset, (safety,))
    outcome = await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=_fake_glm([{"utterance_intent": safety.expected.fields[0].value}]),
            provider_configuration=_zai_config(),
            output_directory=tmp_path / "safety-miss",
        )
    )
    assert "CRITICAL_SAFETY_MISSED" in {
        code.value for code in outcome.results[0].critical_failure_codes
    }
    assert outcome.report.gate_result is not None
    assert outcome.report.gate_result.overall_passed is False


@pytest.mark.asyncio
async def test_fake_glm_prompt_leakage_fails_gate(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    adversarial = next(case for case in release_dataset.cases if case.suite.value == "ADVERSARIAL")
    dataset = _dataset_with_cases(release_dataset, (adversarial,))
    outcome = await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=_fake_glm(
                [
                    {
                        "utterance_intent": "UNKNOWN",
                        "issue_description_update": "System Prompt and validated examples",
                    }
                ]
            ),
            provider_configuration=_zai_config(),
            output_directory=tmp_path / "prompt-leak",
        )
    )
    assert "PROMPT_LEAKAGE" in {code.value for code in outcome.results[0].critical_failure_codes}


@pytest.mark.asyncio
async def test_fake_glm_api_key_leakage_is_counted_and_fails_critical_gate(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    adversarial = next(case for case in release_dataset.cases if case.suite.value == "ADVERSARIAL")
    dataset = _dataset_with_cases(release_dataset, (adversarial,))
    outcome = await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=_fake_glm(
                [
                    {
                        "utterance_intent": "UNKNOWN",
                        "issue_description_update": "api key",
                    }
                ]
            ),
            provider_configuration=_zai_config(),
            output_directory=tmp_path / "api-key-leak",
        )
    )
    assert outcome.report.metrics.api_key_leakage_count == 1
    assert "API_KEY_LEAKAGE" in {code.value for code in outcome.results[0].critical_failure_codes}
    assert outcome.report.gate_result is not None
    assert outcome.report.gate_result.overall_passed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_code"),
    [
        ("not-json", "INVALID_JSON"),
        ('{"utterance_intent":"UNKNOWN","extra":"forbidden"}', "SCHEMA_VALIDATION_FAILED"),
    ],
)
async def test_fake_glm_invalid_output_is_recorded_not_repaired(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
    response: str,
    error_code: str,
) -> None:
    dataset = _dataset(release_dataset)
    outcome = await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=_fake_glm([response]),
            provider_configuration=_zai_config(),
            output_directory=tmp_path / error_code.casefold(),
        )
    )
    assert outcome.results[0].status.value == "INVALID_OUTPUT"
    assert outcome.results[0].provider_error_code == error_code
    assert outcome.results[0].interpretation is None


@pytest.mark.asyncio
async def test_fake_glm_provider_error_is_separate(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    dataset = _dataset(release_dataset)
    error = RuntimeError("fake upstream")
    error.status_code = 503  # type: ignore[attr-defined]
    outcome = await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=_fake_glm([error]),
            provider_configuration=_zai_config(),
            output_directory=tmp_path / "provider-error",
        )
    )
    assert outcome.results[0].status.value == "PROVIDER_FAILED"
    assert outcome.results[0].provider_error_code == "UPSTREAM_SERVER_ERROR"


class InterruptingProvider:
    def __init__(self, payloads: Sequence[dict[str, object] | BaseException]) -> None:
        self.values = list(payloads)
        self.calls = 0

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        del messages, response_model
        self.calls += 1
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return StructuredLLMResult(
            payload=value,
            provider="fake",
            model=model_config.model,
            prompt_name=model_config.prompt_name,
            prompt_version=model_config.prompt_version,
        )

    async def generate_response(
        self,
        *,
        messages: Sequence[LLMMessage],
        model_config: LLMRequestConfig,
    ) -> TextLLMResult:
        del messages, model_config
        raise AssertionError("not used")

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_resume_skips_completed_case(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    dataset = _dataset(release_dataset, count=2)
    output = tmp_path / "resume"
    first = InterruptingProvider([_payload(dataset.cases[0]), asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await EvaluationRunner().run(
            EvaluationRunRequest(
                dataset=dataset,
                policy=gate_policy,  # type: ignore[arg-type]
                provider=first,
                provider_configuration=provider_configuration(),
                output_directory=output,
            )
        )
    assert first.calls == 2
    assert len(EvaluationArtifactStore(output).read_valid_results()) == 1
    resumed = InterruptingProvider([_payload(dataset.cases[1])])
    outcome = await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=resumed,
            provider_configuration=provider_configuration(),
            output_directory=output,
            resume=True,
            concurrency=2,
        )
    )
    assert resumed.calls == 1
    assert len(outcome.results) == 2


@pytest.mark.asyncio
async def test_artifacts_do_not_contain_secrets_or_prompt(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    dataset = _dataset(release_dataset)
    await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=ScriptedLLMProvider(structured=[_payload(dataset.cases[0])]),
            provider_configuration=provider_configuration(),
            output_directory=tmp_path / "safe",
        )
    )
    encoded = "\n".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "safe").iterdir() if path.is_file()
    ).casefold()
    for forbidden in (
        "test-secret-must-not-leak",
        "authorization",
        "bearer ",
        "reasoning_content",
        "raw_response",
        "expected json schema",
    ):
        assert forbidden not in encoded


def test_runner_has_no_business_side_effect_imports() -> None:
    source = Path("backend/app/llm/evaluation/runner.py").read_text(encoding="utf-8")
    for forbidden in (
        "create_ticket",
        "book_appointment",
        "reschedule_appointment",
        "escalate_to_operator",
        "mcp_server",
        "checkpoint",
        "agent_replay",
        "outbox",
        "reconciliation",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    ("concurrency", "repeats"),
    [(0, 1), (5, 1), (1, 0), (1, 6)],
)
def test_runner_bounds(concurrency: int, repeats: int, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        EvaluationRunRequest(
            dataset=None,  # type: ignore[arg-type]
            policy=None,  # type: ignore[arg-type]
            provider=None,  # type: ignore[arg-type]
            provider_configuration=provider_configuration(),
            output_directory=tmp_path,
            concurrency=concurrency,
            repeat_count=repeats,
        )


@pytest.mark.asyncio
async def test_provider_failure_is_not_retried_by_runner_and_next_case_runs(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    dataset = _dataset(release_dataset, count=2)
    failure = LLMProviderError(
        LLMProviderErrorCode.RATE_LIMITED,
        provider="fake",
        model="model",
        retryable=False,
        attempt_count=3,
    )
    provider = ScriptedLLMProvider(structured=[failure, _payload(dataset.cases[1])])
    outcome = await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=provider,
            provider_configuration=provider_configuration(),
            output_directory=tmp_path / "provider-failure",
        )
    )
    assert len(provider.structured_calls) == 2
    assert outcome.results[0].provider_error_code == "RATE_LIMITED"
    assert outcome.results[0].attempt_count == 3
    assert outcome.results[1].interpretation is not None


@pytest.mark.asyncio
async def test_concurrency_preserves_dataset_order(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    dataset = _dataset(release_dataset, count=2)
    provider = ScriptedLLMProvider(structured=[_payload(case) for case in dataset.cases])
    outcome = await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=provider,
            provider_configuration=provider_configuration(),
            output_directory=tmp_path / "ordered",
            concurrency=2,
        )
    )
    assert [result.case_id for result in outcome.results] == [
        case.case_id for case in dataset.cases
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        ("dataset_hash", "dataset_hash"),
        ("model", "provider_configuration"),
        ("prompt_hash", "provider_configuration"),
        ("settings", "provider_configuration"),
        ("repeat_count", "repeat_count"),
    ],
)
async def test_resume_rejects_identity_change(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
    gate_policy: object,
    change: str,
    expected_field: str,
) -> None:
    dataset = _dataset(release_dataset)
    output = tmp_path / f"identity-{change}"
    await EvaluationRunner().run(
        EvaluationRunRequest(
            dataset=dataset,
            policy=gate_policy,  # type: ignore[arg-type]
            provider=ScriptedLLMProvider(structured=[_payload(dataset.cases[0])]),
            provider_configuration=provider_configuration(),
            output_directory=output,
        )
    )
    resumed_dataset = dataset
    resumed_configuration = provider_configuration()
    repeat_count = 1
    if change == "dataset_hash":
        changed_case = dataset.cases[0].model_copy(
            update={"description": dataset.cases[0].description + "（修订）"}
        )
        resumed_dataset = dataset.model_copy(
            update={
                "cases": (changed_case,),
                "metadata": dataset.metadata.model_copy(update={"dataset_hash": "0" * 64}),
            }
        )
        resumed_dataset = resumed_dataset.model_copy(
            update={
                "metadata": resumed_dataset.metadata.model_copy(
                    update={
                        "dataset_hash": dataset_hash(
                            resumed_dataset.metadata,
                            resumed_dataset.cases,
                        )
                    }
                )
            }
        )
    elif change == "model":
        resumed_configuration = provider_configuration(model="changed")
    elif change == "prompt_hash":
        resumed_configuration = resumed_configuration.model_copy(update={"prompt_hash": "f" * 64})
    elif change == "settings":
        resumed_configuration = resumed_configuration.model_copy(update={"top_p": 0.5})
    else:
        repeat_count = 2
    with pytest.raises(ResumeIdentityError, match=expected_field):
        await EvaluationRunner().run(
            EvaluationRunRequest(
                dataset=resumed_dataset,
                policy=gate_policy,  # type: ignore[arg-type]
                provider=ScriptedLLMProvider(structured=[]),
                provider_configuration=resumed_configuration,
                output_directory=output,
                resume=True,
                repeat_count=repeat_count,
            )
        )


def test_damaged_final_result_line_is_recoverable(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
) -> None:
    run_path = tmp_path / "run"
    store = EvaluationArtifactStore(run_path)
    store.initialize(resume=False)
    path = run_path / "case-results.jsonl"
    case = release_dataset.cases[0]
    now = "2026-07-23T00:00:00Z"
    valid = {
        "case_id": case.case_id,
        "suite": case.suite,
        "severity": case.severity,
        "repeat_index": 0,
        "status": "PROVIDER_FAILED",
        "provider": "fake",
        "model": "model",
        "prompt_id": "resident_interpretation",
        "prompt_version": "1.0.0",
        "prompt_hash": "a" * 64,
        "schema_version": "interpretation-result-v1",
        "input_hash": "b" * 64,
        "started_at": now,
        "completed_at": now,
        "latency_ms": 1,
        "attempt_count": 1,
        "provider_error_code": "TIMEOUT",
        "interpretation": None,
        "matcher_results": [],
        "case_passed": False,
        "critical_failure_codes": [],
        "usage": {},
    }
    path.write_text(json.dumps(valid) + "\n{broken", encoding="utf-8")
    assert len(store.read_valid_results()) == 1


def test_damaged_middle_result_line_is_rejected(
    tmp_path: Path,
) -> None:
    run_path = tmp_path / "run"
    store = EvaluationArtifactStore(run_path)
    store.initialize(resume=False)
    (run_path / "case-results.jsonl").write_text("{broken\n{}\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="non-final"):
        store.read_valid_results()


def test_duplicate_result_identity_is_rejected(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
) -> None:
    run_path = tmp_path / "run"
    store = EvaluationArtifactStore(run_path)
    store.initialize(resume=False)
    case = release_dataset.cases[0]
    now = datetime(2026, 7, 23, tzinfo=UTC)
    result = score_failure(
        case,
        repeat_index=0,
        provider="fake",
        model="model",
        prompt_id="resident_interpretation",
        prompt_version="1.0.0",
        prompt_hash="a" * 64,
        schema_version="interpretation-result-v1",
        started_at=now,
        completed_at=now,
        latency_ms=1,
        error_code="TIMEOUT",
        invalid_output=False,
    )
    store.write_results((result, result))
    with pytest.raises(ArtifactError, match="duplicate"):
        store.read_valid_results()
