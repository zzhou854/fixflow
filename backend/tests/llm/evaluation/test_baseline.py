from pathlib import Path

import app.llm.evaluation.baseline as baseline_module
import pytest
from app.llm.evaluation.artifacts import atomic_write_text
from app.llm.evaluation.baseline import (
    BASELINE_FILES,
    ModelReleaseCandidateManifest,
    publish_baseline,
    publish_release_candidate,
    verify_baseline,
)
from app.llm.evaluation.errors import ArtifactError
from app.llm.evaluation.models import EvaluationCaseResult, EvaluationReport
from app.llm.evaluation.qualification import QualificationDecision, qualify
from pydantic import ValidationError
from tests.llm.evaluation.test_qualification import HASHES, _report, _results


def _qualified() -> tuple[
    EvaluationReport,
    tuple[EvaluationCaseResult, ...],
    QualificationDecision,
]:
    report = _report()
    results = _results()
    decision = qualify(
        report,
        results,
        source_hashes=HASHES,
        network_authorized=True,
        cost_acknowledged=True,
    )
    return report, results, decision


def test_qualified_baseline_is_atomic_safe_and_verifiable(tmp_path: Path) -> None:
    report, results, decision = _qualified()
    root = tmp_path / "baselines"
    target = root / "baseline-1"
    manifest = publish_baseline(
        report,
        results,
        decision,
        output_directory=target,
        allowed_root=root,
    )
    assert {path.name for path in target.iterdir()} == BASELINE_FILES
    assert verify_baseline(target) == manifest
    outcomes = (target / "case-outcomes.jsonl").read_text(encoding="utf-8")
    assert "interpretation_fingerprint" in outcomes
    assert "issue_description_update" not in outcomes
    assert "current_user_message" not in outcomes
    assert "api_key" not in outcomes


def test_repeated_identical_baseline_publication_is_idempotent(tmp_path: Path) -> None:
    report, results, decision = _qualified()
    root = tmp_path / "baselines"
    target = root / "baseline-1"
    first = publish_baseline(
        report,
        results,
        decision,
        output_directory=target,
        allowed_root=root,
    )
    second = publish_baseline(
        report,
        results,
        decision,
        output_directory=target,
        allowed_root=root,
    )
    assert first == second


def test_existing_different_baseline_is_never_overwritten(tmp_path: Path) -> None:
    report, results, decision = _qualified()
    root = tmp_path / "baselines"
    target = root / "baseline-1"
    publish_baseline(
        report,
        results,
        decision,
        output_directory=target,
        allowed_root=root,
    )
    (target / "README.md").write_text("different\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="different content"):
        publish_baseline(
            report,
            results,
            decision,
            output_directory=target,
            allowed_root=root,
        )


def test_checksum_tampering_is_detected(tmp_path: Path) -> None:
    report, results, decision = _qualified()
    root = tmp_path / "baselines"
    target = root / "baseline-1"
    publish_baseline(
        report,
        results,
        decision,
        output_directory=target,
        allowed_root=root,
    )
    (target / "README.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="mismatch"):
        verify_baseline(target)


def test_baseline_publication_rejects_path_traversal(tmp_path: Path) -> None:
    report, results, decision = _qualified()
    root = tmp_path / "baselines"
    with pytest.raises(ArtifactError, match="escapes"):
        publish_baseline(
            report,
            results,
            decision,
            output_directory=root / ".." / "escaped",
            allowed_root=root,
        )


def test_partial_baseline_publication_is_cleaned_up_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, results, decision = _qualified()
    root = tmp_path / "baselines"
    target = root / "baseline-1"
    original = atomic_write_text
    calls = 0

    def fail_during_publication(path: Path, content: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated local write failure")
        original(path, content)

    monkeypatch.setattr(
        "app.llm.evaluation.baseline.atomic_write_text",
        fail_during_publication,
    )
    with pytest.raises(OSError, match="simulated"):
        publish_baseline(
            report,
            results,
            decision,
            output_directory=target,
            allowed_root=root,
        )
    assert not target.exists()
    assert not tuple(root.glob(".baseline-1.*"))


def test_not_qualified_run_cannot_publish_baseline(tmp_path: Path) -> None:
    report, results, _ = _qualified()
    failed = qualify(
        report.model_copy(
            update={
                "gate_result": report.gate_result.model_copy(
                    update={"absolute_gate_passed": False, "overall_passed": False}
                )
                if report.gate_result
                else None
            }
        ),
        results,
        source_hashes=HASHES,
        network_authorized=True,
        cost_acknowledged=True,
    )
    with pytest.raises(ArtifactError, match="did not permit"):
        publish_baseline(
            report,
            results,
            failed,
            output_directory=tmp_path / "baselines" / "baseline-1",
            allowed_root=tmp_path / "baselines",
        )


def test_release_candidate_references_verified_baseline_and_is_idempotent(
    tmp_path: Path,
) -> None:
    report, results, decision = _qualified()
    baseline_root = tmp_path / "baselines"
    baseline = publish_baseline(
        report,
        results,
        decision,
        output_directory=baseline_root / "baseline-1",
        allowed_root=baseline_root,
    )
    release_root = tmp_path / "releases"
    target = release_root / "rc1.json"
    first = publish_release_candidate(
        baseline_root / "baseline-1",
        output_path=target,
        allowed_root=release_root,
    )
    second = publish_release_candidate(
        baseline_root / "baseline-1",
        output_path=target,
        allowed_root=release_root,
    )
    assert first == second
    assert first.activation_status.value == "NOT_ACTIVATED"
    assert first.baseline_id == baseline.baseline_id
    assert len(first.known_limitations) >= 7


def test_release_candidate_rejects_different_existing_content(tmp_path: Path) -> None:
    report, results, decision = _qualified()
    baseline_root = tmp_path / "baselines"
    publish_baseline(
        report,
        results,
        decision,
        output_directory=baseline_root / "baseline-1",
        allowed_root=baseline_root,
    )
    release_root = tmp_path / "releases"
    target = release_root / "rc1.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="different content"):
        publish_release_candidate(
            baseline_root / "baseline-1",
            output_path=target,
            allowed_root=release_root,
        )


def test_release_candidate_requires_an_existing_verified_baseline(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        publish_release_candidate(
            tmp_path / "baselines" / "missing",
            output_path=tmp_path / "releases" / "rc1.json",
            allowed_root=tmp_path / "releases",
        )


def test_release_candidate_rejects_tampered_baseline(tmp_path: Path) -> None:
    report, results, decision = _qualified()
    baseline_root = tmp_path / "baselines"
    target = baseline_root / "baseline-1"
    publish_baseline(
        report,
        results,
        decision,
        output_directory=target,
        allowed_root=baseline_root,
    )
    (target / "README.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="mismatch"):
        publish_release_candidate(
            target,
            output_path=tmp_path / "releases" / "rc1.json",
            allowed_root=tmp_path / "releases",
        )


def test_baseline_and_candidate_models_reject_secret_fields() -> None:
    report, results, decision = _qualified()
    assert report.manifest.completed_at_utc is not None
    baseline_payload = baseline_module._baseline_manifest(
        report,
        decision,
        completed_at=report.manifest.completed_at_utc,
        artifact_hashes=baseline_module._source_hashes(decision),
    ).model_dump(mode="json")
    baseline_payload["api_key"] = "must-never-be-accepted"
    with pytest.raises(ValidationError):
        baseline_module.ModelBaselineManifest.model_validate(baseline_payload)


def test_release_candidate_requires_all_known_limitations() -> None:
    report, results, decision = _qualified()
    assert report.manifest.completed_at_utc is not None
    baseline_payload = baseline_module._baseline_manifest(
        report,
        decision,
        completed_at=report.manifest.completed_at_utc,
        artifact_hashes=baseline_module._source_hashes(decision),
    )
    payload = baseline_module.ModelReleaseCandidateManifest(
        baseline_id=baseline_payload.baseline_id,
        baseline_version=baseline_payload.baseline_version,
        qualified_runtime_commit=baseline_payload.qualified_runtime_commit,
        provider=baseline_payload.provider,
        model=baseline_payload.model,
        prompt_id=baseline_payload.prompt_id,
        prompt_version=baseline_payload.prompt_version,
        prompt_hash=baseline_payload.prompt_hash,
        dataset_id=baseline_payload.dataset_id,
        dataset_version=baseline_payload.dataset_version,
        dataset_hash=baseline_payload.dataset_hash,
        policy_id=baseline_payload.policy_id,
        policy_version=baseline_payload.policy_version,
        policy_hash=baseline_payload.policy_hash,
        qualification_status="QUALIFIED",
        known_limitations=baseline_module.KNOWN_LIMITATIONS,
        created_at_utc=baseline_payload.created_at_utc,
    ).model_dump(mode="json")
    payload["known_limitations"] = payload["known_limitations"][:-1]
    with pytest.raises(ValidationError):
        baseline_module.ModelReleaseCandidateManifest.model_validate(payload)


def test_release_candidate_schema_forbids_production_activation() -> None:
    with pytest.raises(ValidationError):
        ModelReleaseCandidateManifest.model_validate(
            {
                "release_candidate_id": "glm-5-1-resident-interpretation-rc1",
                "release_candidate_version": "1.0.0",
                "release_candidate_schema_version": "model-release-candidate-v1",
                "status": "QUALIFIED_CANDIDATE",
                "activation_status": "PRODUCTION_ACTIVE",
            }
        )
