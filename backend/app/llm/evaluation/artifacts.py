"""Atomic, local-only evaluation artifacts with safe projections."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from app.llm.evaluation.errors import ArtifactError
from app.llm.evaluation.hashing import sha256_text
from app.llm.evaluation.models import (
    EvaluationArtifactIndex,
    EvaluationCaseResult,
    EvaluationGateResult,
    EvaluationReport,
    EvaluationRunManifest,
)

ARTIFACT_FILES = (
    "manifest.json",
    "case-results.jsonl",
    "summary.json",
    "summary.md",
    "failures.jsonl",
    "gate-result.json",
    "artifact-index.json",
)


class EvaluationArtifactStore:
    def __init__(self, run_directory: Path) -> None:
        self.run_directory = run_directory

    def initialize(self, *, resume: bool) -> None:
        if self.run_directory.exists() and not resume:
            raise ArtifactError("output directory already exists; use --resume")
        self.run_directory.mkdir(parents=True, exist_ok=resume)

    def write_manifest(self, manifest: EvaluationRunManifest) -> None:
        self._atomic_text("manifest.json", manifest.model_dump_json(indent=2) + "\n")

    def read_manifest(self) -> EvaluationRunManifest:
        try:
            return EvaluationRunManifest.model_validate_json(
                (self.run_directory / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise ArtifactError("manifest is missing or damaged") from exc

    def write_results(self, results: tuple[EvaluationCaseResult, ...]) -> None:
        content = "".join(result.model_dump_json() + "\n" for result in results)
        self._atomic_text("case-results.jsonl", content)

    def read_valid_results(self) -> tuple[EvaluationCaseResult, ...]:
        path = self.run_directory / "case-results.jsonl"
        if not path.exists():
            return ()
        results: list[EvaluationCaseResult] = []
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            try:
                results.append(EvaluationCaseResult.model_validate_json(line))
            except ValidationError:
                if index == len(lines) - 1:
                    break
                raise ArtifactError("case results contain a damaged non-final line") from None
        identities = [(item.case_id, item.repeat_index) for item in results]
        if len(identities) != len(set(identities)):
            raise ArtifactError("case results contain duplicate identities")
        return tuple(results)

    def archive_resume_failures(
        self,
        results: tuple[EvaluationCaseResult, ...],
    ) -> None:
        """Preserve superseded infrastructure failures before a safe resume."""

        if not results:
            return
        path = self.run_directory / "resume-infrastructure-history.jsonl"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        content = existing + "".join(result.model_dump_json() + "\n" for result in results)
        self._atomic_text(path.name, content)

    def finalize(
        self,
        report: EvaluationReport,
        results: tuple[EvaluationCaseResult, ...],
        gate: EvaluationGateResult,
    ) -> EvaluationArtifactIndex:
        self._atomic_text("summary.json", report.model_dump_json(indent=2) + "\n")
        self._atomic_text("summary.md", render_summary(report))
        failures = tuple(item for item in results if not item.case_passed)
        failure_content = "".join(
            json.dumps(
                {
                    "case_id": item.case_id,
                    "repeat_index": item.repeat_index,
                    "status": item.status,
                    "failed_matchers": [
                        {
                            "path": match.path,
                            "matcher": match.matcher,
                            "reason": match.reason,
                        }
                        for match in item.matcher_results
                        if not match.passed
                    ],
                    "critical_failure_codes": item.critical_failure_codes,
                    "provider_error_code": item.provider_error_code,
                    "actual_interpretation": item.interpretation.model_dump(
                        mode="json", exclude_none=True
                    )
                    if item.interpretation
                    else None,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            + "\n"
            for item in failures
        )
        self._atomic_text("failures.jsonl", failure_content)
        self._atomic_text("gate-result.json", gate.model_dump_json(indent=2) + "\n")
        names = tuple(name for name in ARTIFACT_FILES if name != "artifact-index.json")
        resume_history = self.run_directory / "resume-infrastructure-history.jsonl"
        if resume_history.exists():
            names = (*names, resume_history.name)
        hashes = {
            name: sha256_text((self.run_directory / name).read_text(encoding="utf-8"))
            for name in names
        }
        index = EvaluationArtifactIndex(
            artifact_schema_version="evaluation-artifact-index-v1",
            run_id=report.manifest.run_id,
            files=(*names, "artifact-index.json"),
            file_hashes=hashes,
        )
        self._atomic_text("artifact-index.json", index.model_dump_json(indent=2) + "\n")
        return index

    def _atomic_text(self, name: str, content: str) -> None:
        atomic_write_text(self.run_directory / name, content)


def atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, target)
    except OSError as exc:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise ArtifactError(f"failed to atomically write {target.name}") from exc


def render_summary(report: EvaluationReport) -> str:
    manifest, metrics = report.manifest, report.metrics
    gate = report.gate_result
    return "\n".join(
        [
            "# Evaluation Summary",
            "",
            f"- Run: `{manifest.run_id}`",
            f"- Dataset: `{manifest.dataset_id}` `{manifest.dataset_version}`",
            f"- Provider: `{manifest.provider_configuration.provider}`",
            f"- Model: `{manifest.provider_configuration.model}`",
            f"- Prompt: `{manifest.provider_configuration.prompt_id}` "
            f"`{manifest.provider_configuration.prompt_version}`",
            f"- Schema: `{manifest.provider_configuration.interpretation_schema_version}`",
            f"- Scorer: `{manifest.scorer_id}` `{manifest.scorer_version}`",
            f"- Completion: `{metrics.completed_cases}/{metrics.total_cases}`",
            f"- Case pass rate: `{metrics.case_pass_rate:.4f}`",
            f"- Intent accuracy: `{metrics.intent_accuracy:.4f}`",
            f"- Critical safety recall: `{metrics.critical_safety_recall:.4f}`",
            f"- Request-human boundary: `{metrics.request_human_boundary_accuracy:.4f}`",
            f"- p95 latency ms: `{metrics.p95_latency_ms}`",
            f"- Observed token cases: `{metrics.usage_observed_cases}`",
            f"- Provider failures: `{metrics.provider_failure_counts}`",
            f"- Gate: `{'PASSED' if gate and gate.overall_passed else 'FAILED'}`",
            f"- Critical failures: `{list(gate.critical_failures) if gate else []}`",
            "",
            "Generated from machine-readable results. It contains no prompt, raw response, "
            "credential material, or full input text.",
            "",
        ]
    )
