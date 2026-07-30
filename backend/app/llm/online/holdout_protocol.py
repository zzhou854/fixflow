"""Offline-only tooling for sealing a future, previously unseen Holdout corpus."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HoldoutStatus(StrEnum):
    DRAFT = "DRAFT"
    SEALED = "SEALED"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


class HoldoutManifest(BaseModel):
    """Identity and lifecycle only; the manifest never contains case text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    dataset_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    case_count: int = Field(ge=1, le=10_000)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scorer_version: str = Field(min_length=1, max_length=50)
    gate_version: str = Field(min_length=1, max_length=50)
    prompt_version: str = Field(min_length=1, max_length=50)
    schema_version: str = Field(min_length=1, max_length=100)
    created_at: datetime
    locked_at: datetime | None = None
    first_live_call_at: datetime | None = None
    live_call_count: int = Field(default=0, ge=0)
    status: HoldoutStatus = HoldoutStatus.DRAFT

    @model_validator(mode="after")
    def validate_lifecycle(self) -> HoldoutManifest:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.status is HoldoutStatus.DRAFT:
            if self.locked_at is not None or self.first_live_call_at is not None:
                raise ValueError("draft Holdout cannot have lock or live-call timestamps")
            if self.live_call_count:
                raise ValueError("draft Holdout cannot have live calls")
        elif self.status is HoldoutStatus.SEALED:
            if self.locked_at is None or self.first_live_call_at is not None:
                raise ValueError("sealed Holdout requires only locked_at")
            if self.live_call_count:
                raise ValueError("sealed Holdout cannot have live calls")
        elif self.status is HoldoutStatus.CONSUMED:
            if self.locked_at is None or self.first_live_call_at is None:
                raise ValueError("consumed Holdout requires lock and first-call timestamps")
            if self.live_call_count < 1:
                raise ValueError("consumed Holdout requires at least one live call")
        return self


class DuplicateScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_case_count: int = Field(ge=0)
    duplicate_case_ids: tuple[str, ...] = ()
    exact_text_duplicates: tuple[str, ...] = ()
    normalized_text_duplicates: tuple[str, ...] = ()
    known_fingerprint_duplicates: tuple[str, ...] = ()

    @property
    def isolated(self) -> bool:
        return not (
            self.duplicate_case_ids
            or self.exact_text_duplicates
            or self.normalized_text_duplicates
            or self.known_fingerprint_duplicates
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_draft_manifest(
    *,
    dataset_path: Path,
    dataset_id: str,
    dataset_version: str,
    scorer_version: str,
    gate_version: str,
    prompt_version: str,
    schema_version: str,
    now: datetime | None = None,
) -> HoldoutManifest:
    cases = _read_cases(dataset_path)
    return HoldoutManifest(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        case_count=len(cases),
        dataset_sha256=sha256_file(dataset_path),
        scorer_version=scorer_version,
        gate_version=gate_version,
        prompt_version=prompt_version,
        schema_version=schema_version,
        created_at=now or datetime.now(UTC),
    )


def seal_manifest(
    manifest: HoldoutManifest,
    *,
    dataset_path: Path,
    duplicates: DuplicateScanResult,
    now: datetime | None = None,
) -> HoldoutManifest:
    if manifest.status is not HoldoutStatus.DRAFT:
        raise ValueError("only a draft Holdout can be sealed")
    _verify_dataset_identity(manifest, dataset_path)
    if not duplicates.isolated:
        raise ValueError("Holdout duplicates known development or historical cases")
    return manifest.model_copy(
        update={"status": HoldoutStatus.SEALED, "locked_at": now or datetime.now(UTC)}
    )


def consume_for_first_live_call(
    manifest: HoldoutManifest,
    *,
    dataset_path: Path,
    now: datetime | None = None,
) -> HoldoutManifest:
    if manifest.status is not HoldoutStatus.SEALED:
        raise ValueError("only a sealed Holdout can be used for qualification")
    _verify_dataset_identity(manifest, dataset_path)
    timestamp = now or datetime.now(UTC)
    return manifest.model_copy(
        update={
            "status": HoldoutStatus.CONSUMED,
            "first_live_call_at": timestamp,
            "live_call_count": 1,
        }
    )


def record_consumed_call(manifest: HoldoutManifest) -> HoldoutManifest:
    if manifest.status is not HoldoutStatus.CONSUMED:
        raise ValueError("live-call accounting requires a consumed Holdout")
    return manifest.model_copy(update={"live_call_count": manifest.live_call_count + 1})


def invalidate_if_changed(
    manifest: HoldoutManifest,
    *,
    dataset_path: Path,
    scorer_version: str,
    gate_version: str,
    prompt_version: str,
    schema_version: str,
) -> HoldoutManifest:
    changed = (
        sha256_file(dataset_path) != manifest.dataset_sha256
        or scorer_version != manifest.scorer_version
        or gate_version != manifest.gate_version
        or prompt_version != manifest.prompt_version
        or schema_version != manifest.schema_version
    )
    return (
        manifest.model_copy(update={"status": HoldoutStatus.INVALIDATED}) if changed else manifest
    )


def scan_duplicates(
    candidate_path: Path,
    *,
    reference_paths: Iterable[Path],
    known_fingerprints: Iterable[str] = (),
) -> DuplicateScanResult:
    candidate = _read_cases(candidate_path)
    references = [item for path in reference_paths for item in _read_cases(path)]
    candidate_ids = _values(candidate, "case_id")
    reference_ids = _values(references, "case_id")
    candidate_texts = _messages(candidate)
    reference_texts = _messages(references)
    normalized_reference = {_normalize_text(value) for value in reference_texts}
    known = set(known_fingerprints)
    return DuplicateScanResult(
        candidate_case_count=len(candidate),
        duplicate_case_ids=tuple(sorted(candidate_ids & reference_ids)),
        exact_text_duplicates=tuple(sorted(set(candidate_texts) & set(reference_texts))),
        normalized_text_duplicates=tuple(
            sorted(
                value for value in candidate_texts if _normalize_text(value) in normalized_reference
            )
        ),
        known_fingerprint_duplicates=tuple(
            sorted(
                value
                for value in candidate_texts
                if hashlib.sha256(_normalize_text(value).encode()).hexdigest() in known
            )
        ),
    )


def append_audit_record(path: Path, record: BaseModel) -> None:
    """Append validated evidence; never overwrite an existing audit stream."""

    path.parent.mkdir(parents=True, exist_ok=True)
    line = record.model_dump_json() + "\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line.encode())
    finally:
        os.close(descriptor)


def _verify_dataset_identity(manifest: HoldoutManifest, path: Path) -> None:
    if sha256_file(path) != manifest.dataset_sha256:
        raise ValueError("Holdout content changed after manifest creation")
    if len(_read_cases(path)) != manifest.case_count:
        raise ValueError("Holdout case count changed after manifest creation")


def _read_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as source:
        for number, line in enumerate(source, start=1):
            if not line.strip():
                raise ValueError(f"blank Holdout JSONL line at {number}")
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"Holdout JSONL object required at {number}")
            cases.append(raw)
    if not cases:
        raise ValueError("Holdout dataset cannot be empty")
    return cases


def _values(cases: Iterable[dict[str, Any]], field: str) -> set[str]:
    return {value for case in cases if isinstance((value := case.get(field)), str)}


def _messages(cases: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    messages: list[str] = []
    for case in cases:
        input_value = case.get("input")
        if not isinstance(input_value, dict):
            raise ValueError("Holdout case input must be an object")
        message = input_value.get("current_user_message")
        if not isinstance(message, str) or not message:
            raise ValueError("Holdout case requires current_user_message")
        messages.append(message)
    return tuple(messages)


def _normalize_text(value: str) -> str:
    return "".join(value.casefold().split())
