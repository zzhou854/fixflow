"""Offline validation and sealing for externally stored Holdout assets."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.llm.online.holdout_identity import grounded_identity, structured_identity
from app.llm.online.holdout_protocol import (
    DuplicateScanResult,
    HoldoutManifest,
    create_draft_manifest,
    create_pending_approval_packet,
    manifest_sha256,
    scan_duplicates,
    seal_manifest,
)
from app.llm.online.holdout_scoring import GroundedGolden, StructuredGolden


class HoldoutSuite(StrEnum):
    STRUCTURED_UNDERSTANDING = "STRUCTURED_UNDERSTANDING"
    GROUNDED_RESPONSE = "GROUNDED_RESPONSE"


class GoldenValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suite: HoldoutSuite
    case_count: int = Field(ge=1)
    schema_validation_passed: bool
    rule_consistency_passed: bool
    cross_file_identity_passed: bool
    evidence_span_validation_passed: bool
    independent_human_review_completed: bool = False
    status: str = "REVIEW_PENDING"


class HoldoutPreparationSummary(BaseModel):
    """Safe summary only; it intentionally contains no Case IDs or text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary_schema_version: str = "holdout-preparation-summary-v1"
    suite: HoldoutSuite
    dataset_id: str
    dataset_version: str
    case_count: int
    single_turn_count: int
    multi_turn_count: int
    category_distribution: Mapping[str, int]
    dataset_sha256: str
    golden_sha256: str
    manifest_sha256: str
    duplicate_scan: DuplicateScanResult
    golden_validation: GoldenValidationReport
    contains_real_personal_data: bool = False
    live_call_count: int = 0


def prepare_structured_suite(
    *,
    dataset_path: Path,
    golden_path: Path,
    reference_jsonl_paths: Iterable[Path],
    reference_text_paths: Iterable[Path],
    code_commit: str,
    output_manifest_path: Path,
    dataset_version: str = "2.0.0",
    near_duplicate_threshold: float = 0.88,
    now: datetime | None = None,
) -> HoldoutPreparationSummary:
    validation, counts = validate_structured_assets(dataset_path, golden_path)
    duplicates = scan_duplicates(
        dataset_path,
        reference_paths=reference_jsonl_paths,
        reference_text_paths=reference_text_paths,
        near_duplicate_threshold=near_duplicate_threshold,
    )
    manifest = create_draft_manifest(
        dataset_path=dataset_path,
        golden_path=golden_path,
        dataset_id="resident_interpretation_holdout",
        dataset_version=dataset_version,
        single_turn_count=counts["single"],
        multi_turn_count=counts["multi"],
        category_distribution=counts["categories"],
        identity=structured_identity(code_commit=code_commit),
        now=now,
    )
    sealed = seal_manifest(
        manifest,
        dataset_path=dataset_path,
        golden_path=golden_path,
        duplicates=duplicates,
        now=now,
    )
    _atomic_write_model(output_manifest_path, sealed)
    return _summary(
        HoldoutSuite.STRUCTURED_UNDERSTANDING,
        sealed,
        duplicates,
        validation,
    )


def prepare_grounded_suite(
    *,
    dataset_path: Path,
    golden_path: Path,
    reference_jsonl_paths: Iterable[Path],
    reference_text_paths: Iterable[Path],
    code_commit: str,
    output_manifest_path: Path,
    dataset_version: str = "1.0.0",
    near_duplicate_threshold: float = 0.88,
    now: datetime | None = None,
) -> HoldoutPreparationSummary:
    validation, counts = validate_grounded_assets(dataset_path, golden_path)
    duplicates = scan_duplicates(
        dataset_path,
        reference_paths=reference_jsonl_paths,
        reference_text_paths=reference_text_paths,
        near_duplicate_threshold=near_duplicate_threshold,
    )
    manifest = create_draft_manifest(
        dataset_path=dataset_path,
        golden_path=golden_path,
        dataset_id="grounded_response_holdout",
        dataset_version=dataset_version,
        single_turn_count=counts["single"],
        multi_turn_count=counts["multi"],
        category_distribution=counts["categories"],
        identity=grounded_identity(code_commit=code_commit),
        now=now,
    )
    sealed = seal_manifest(
        manifest,
        dataset_path=dataset_path,
        golden_path=golden_path,
        duplicates=duplicates,
        now=now,
    )
    _atomic_write_model(output_manifest_path, sealed)
    return _summary(HoldoutSuite.GROUNDED_RESPONSE, sealed, duplicates, validation)


def write_pending_approval_material(
    *,
    structured_manifest_path: Path,
    grounded_manifest_path: Path,
    output_path: Path,
    now: datetime | None = None,
) -> None:
    structured = HoldoutManifest.model_validate_json(
        structured_manifest_path.read_text(encoding="utf-8")
    )
    grounded = HoldoutManifest.model_validate_json(
        grounded_manifest_path.read_text(encoding="utf-8")
    )
    packet = create_pending_approval_packet(
        structured,
        grounded,
        generated_at=now or datetime.now(UTC),
    )
    _atomic_write_model(output_path, packet)


def validate_structured_assets(
    dataset_path: Path,
    golden_path: Path,
) -> tuple[GoldenValidationReport, dict[str, Any]]:
    dataset = _read_jsonl(dataset_path)
    golden_rows = _read_jsonl(golden_path)
    golden = tuple(StructuredGolden.model_validate(row) for row in golden_rows)
    _require_matching_ids(dataset, golden_rows)
    by_id = {item.case_id: item for item in golden}
    single = multi = 0
    categories: Counter[str] = Counter()
    evidence_valid = True
    for case in dataset:
        case_id = _case_id(case)
        turns = case.get("conversation_turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"{case_id}: conversation_turns are required")
        golden_turns = tuple(
            (turn.role, turn.content) for turn in by_id[case_id].conversation_turns
        )
        dataset_turns = tuple(
            (turn.get("role"), turn.get("content")) for turn in turns if isinstance(turn, dict)
        )
        if dataset_turns != golden_turns:
            raise ValueError(f"{case_id}: Golden conversation does not match dataset")
        user_turn_count = sum(role == "USER" for role, _ in dataset_turns)
        if user_turn_count > 1:
            multi += 1
        else:
            single += 1
        category = case.get("category")
        if not isinstance(category, str):
            raise ValueError(f"{case_id}: category is required")
        categories[category] += 1
        conversation = "\n".join(
            content for _, content in dataset_turns if isinstance(content, str)
        ).casefold()
        for fact in by_id[case_id].expected_evidence_facts:
            if fact.evidence is None or fact.evidence.casefold() not in conversation:
                evidence_valid = False
        _validate_structured_rules(by_id[case_id])
    if not evidence_valid:
        raise ValueError("one or more Golden evidence spans are unsupported")
    return (
        GoldenValidationReport(
            suite=HoldoutSuite.STRUCTURED_UNDERSTANDING,
            case_count=len(dataset),
            schema_validation_passed=True,
            rule_consistency_passed=True,
            cross_file_identity_passed=True,
            evidence_span_validation_passed=True,
        ),
        {"single": single, "multi": multi, "categories": dict(categories)},
    )


def validate_grounded_assets(
    dataset_path: Path,
    golden_path: Path,
) -> tuple[GoldenValidationReport, dict[str, Any]]:
    dataset = _read_jsonl(dataset_path)
    golden_rows = _read_jsonl(golden_path)
    golden = tuple(GroundedGolden.model_validate(row) for row in golden_rows)
    _require_matching_ids(dataset, golden_rows)
    by_id = {item.case_id: item for item in golden}
    categories: Counter[str] = Counter()
    for case in dataset:
        case_id = _case_id(case)
        input_value = case.get("input")
        if not isinstance(input_value, dict):
            raise ValueError(f"{case_id}: grounded input is required")
        category = case.get("category")
        if not isinstance(category, str):
            raise ValueError(f"{case_id}: category is required")
        categories[category] += 1
        golden_case = by_id[case_id]
        allowed_ids = input_value.get("allowed_fact_ids")
        if not isinstance(allowed_ids, list) or set(allowed_ids) != set(
            golden_case.allowed_fact_ids
        ):
            raise ValueError(f"{case_id}: allowed fact identity mismatch")
        if (
            input_value.get("message_outcome") != golden_case.expected_message_outcome
            or input_value.get("required_user_action") != golden_case.expected_required_user_action
        ):
            raise ValueError(f"{case_id}: outcome or required action mismatch")
        if golden_case.safety_template_required and not (
            golden_case.deterministic_template_required
        ):
            raise ValueError(f"{case_id}: safety output must use a deterministic template")
    return (
        GoldenValidationReport(
            suite=HoldoutSuite.GROUNDED_RESPONSE,
            case_count=len(dataset),
            schema_validation_passed=True,
            rule_consistency_passed=True,
            cross_file_identity_passed=True,
            evidence_span_validation_passed=True,
        ),
        {"single": len(dataset), "multi": 0, "categories": dict(categories)},
    )


def _validate_structured_rules(golden: StructuredGolden) -> None:
    if golden.expected_human_boundary and golden.expected_intent != "REQUEST_HUMAN":
        raise ValueError(f"{golden.case_id}: human boundary requires REQUEST_HUMAN")
    if golden.expected_critical_safety and golden.expected_safety_class != "CRITICAL":
        raise ValueError(f"{golden.case_id}: critical safety class mismatch")
    if golden.expected_safety_class == "CRITICAL" and not golden.expected_critical_safety:
        raise ValueError(f"{golden.case_id}: critical safety flag mismatch")
    if len(set(golden.expected_missing_fields)) != len(golden.expected_missing_fields):
        raise ValueError(f"{golden.case_id}: duplicate Missing Fields")
    fact_fields = {item.field for item in golden.expected_evidence_facts}
    if fact_fields.intersection(golden.expected_null_fields):
        raise ValueError(f"{golden.case_id}: field cannot be fact and null")


def _summary(
    suite: HoldoutSuite,
    manifest: HoldoutManifest,
    duplicates: DuplicateScanResult,
    validation: GoldenValidationReport,
) -> HoldoutPreparationSummary:
    return HoldoutPreparationSummary(
        suite=suite,
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        case_count=manifest.case_count,
        single_turn_count=manifest.single_turn_count,
        multi_turn_count=manifest.multi_turn_count,
        category_distribution={item.label: item.count for item in manifest.category_distribution},
        dataset_sha256=manifest.dataset_sha256,
        golden_sha256=manifest.golden_sha256,
        manifest_sha256=manifest_sha256(manifest),
        duplicate_scan=duplicates,
        golden_validation=validation,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        if not line.strip():
            raise ValueError(f"{path}:{number}: blank JSONL line")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number}: JSON object required")
        rows.append(value)
    if not rows:
        raise ValueError(f"{path}: JSONL cannot be empty")
    return rows


def _require_matching_ids(
    dataset: Iterable[Mapping[str, Any]],
    golden: Iterable[Mapping[str, Any]],
) -> None:
    dataset_ids = {_case_id(item) for item in dataset}
    golden_ids = {_case_id(item) for item in golden}
    if len(dataset_ids) != len(tuple(dataset)) or len(golden_ids) != len(tuple(golden)):
        raise ValueError("Holdout Case IDs must be unique")
    if dataset_ids != golden_ids:
        raise ValueError("Holdout dataset and Golden Case IDs differ")


def _case_id(value: Mapping[str, Any]) -> str:
    case_id = value.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("Holdout case_id is required")
    return case_id


def _atomic_write_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(model.model_dump_json(indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


__all__ = [
    "GoldenValidationReport",
    "HoldoutPreparationSummary",
    "HoldoutSuite",
    "prepare_grounded_suite",
    "prepare_structured_suite",
    "validate_grounded_assets",
    "validate_structured_assets",
    "write_pending_approval_material",
]
