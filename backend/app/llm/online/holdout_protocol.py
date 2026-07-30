"""Sealed, independently approved, one-time Holdout qualification protocol.

This module never calls an online Provider.  It validates identities and
persists the irreversible ``APPROVED -> CONSUMED`` transition that must happen
before a separately approved executor dispatches its first request.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"
COMMIT_PATTERN = r"^[0-9a-f]{40}$"
NEAR_DUPLICATE_ALGORITHM = "unicode-nfkc-punctuationless-sequence-matcher"
NEAR_DUPLICATE_ALGORITHM_VERSION = "1.0.0"
DEFAULT_NEAR_DUPLICATE_THRESHOLD = 0.88


class HoldoutStatus(StrEnum):
    DRAFT = "DRAFT"
    SEALED = "SEALED"
    APPROVED = "APPROVED"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class GoldenReviewStatus(StrEnum):
    AUTOMATED_CHECKED_REVIEW_PENDING = "AUTOMATED_CHECKED_REVIEW_PENDING"
    INDEPENDENT_REVIEWED = "INDEPENDENT_REVIEWED"


class DistributionCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=100)
    count: int = Field(ge=0)


class FrozenQualificationIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code_commit: str = Field(pattern=COMMIT_PATTERN)
    prompt_version: str = Field(min_length=1, max_length=100)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    schema_version: str = Field(min_length=1, max_length=100)
    schema_sha256: str = Field(pattern=SHA256_PATTERN)
    normalization_version: str = Field(min_length=1, max_length=100)
    normalization_sha256: str = Field(pattern=SHA256_PATTERN)
    scorer_version: str = Field(min_length=1, max_length=100)
    scorer_sha256: str = Field(pattern=SHA256_PATTERN)
    gate_version: str = Field(min_length=1, max_length=100)
    gate_sha256: str = Field(pattern=SHA256_PATTERN)
    provider_router_version: str = Field(min_length=1, max_length=100)


class HoldoutManifest(BaseModel):
    """Content identity and lifecycle only; never contains case or Golden text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_schema_version: Literal["holdout-manifest-v2"] = "holdout-manifest-v2"
    dataset_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    dataset_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    purpose: Literal["HOLDOUT_QUALIFICATION"] = "HOLDOUT_QUALIFICATION"
    case_count: int = Field(ge=1, le=10_000)
    single_turn_count: int = Field(ge=0)
    multi_turn_count: int = Field(ge=0)
    category_distribution: tuple[DistributionCount, ...] = ()
    dataset_sha256: str = Field(pattern=SHA256_PATTERN)
    golden_sha256: str = Field(pattern=SHA256_PATTERN)
    identity: FrozenQualificationIdentity
    golden_review_status: GoldenReviewStatus
    created_at: datetime
    sealed_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by: str | None = Field(default=None, max_length=200)
    first_live_call_at: datetime | None = None
    live_call_count: int = Field(default=0, ge=0)
    status: HoldoutStatus = HoldoutStatus.DRAFT

    @model_validator(mode="after")
    def validate_lifecycle(self) -> HoldoutManifest:
        timestamps = (
            self.created_at,
            self.sealed_at,
            self.approved_at,
            self.first_live_call_at,
        )
        if any(value is not None and value.tzinfo is None for value in timestamps):
            raise ValueError("Holdout timestamps must be timezone-aware")
        if self.single_turn_count + self.multi_turn_count != self.case_count:
            raise ValueError("single-turn and multi-turn counts must equal case_count")
        if (
            self.category_distribution
            and sum(item.count for item in self.category_distribution) != self.case_count
        ):
            raise ValueError("category distribution must equal case_count")
        if self.status is HoldoutStatus.DRAFT:
            if any(
                value is not None
                for value in (
                    self.sealed_at,
                    self.approved_at,
                    self.approved_by,
                    self.first_live_call_at,
                )
            ):
                raise ValueError("draft Holdout cannot have lifecycle timestamps")
            if self.live_call_count:
                raise ValueError("draft Holdout cannot have live calls")
        elif self.status is HoldoutStatus.SEALED:
            if self.sealed_at is None or any(
                value is not None
                for value in (
                    self.approved_at,
                    self.approved_by,
                    self.first_live_call_at,
                )
            ):
                raise ValueError("sealed Holdout requires only sealed_at")
            if self.live_call_count:
                raise ValueError("sealed Holdout cannot have live calls")
        elif self.status is HoldoutStatus.APPROVED:
            if (
                self.sealed_at is None
                or self.approved_at is None
                or self.approved_by is None
                or self.first_live_call_at is not None
                or self.live_call_count
            ):
                raise ValueError("approved Holdout requires approval but no live call")
            if self.golden_review_status is not GoldenReviewStatus.INDEPENDENT_REVIEWED:
                raise ValueError("approved Holdout requires independent Golden review")
        elif self.status is HoldoutStatus.CONSUMED:
            if (
                self.sealed_at is None
                or self.approved_at is None
                or self.approved_by is None
                or self.first_live_call_at is None
                or self.live_call_count < 1
            ):
                raise ValueError("consumed Holdout requires sealing, approval, and a live call")
            if self.golden_review_status is not GoldenReviewStatus.INDEPENDENT_REVIEWED:
                raise ValueError("consumed Holdout requires independent Golden review")
        return self


class NearDuplicateMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_case_id: str
    reference_source: str
    reference_case_id: str | None = None
    similarity: float = Field(ge=0, le=1)


class DuplicateScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_case_count: int = Field(ge=0)
    reference_unit_count: int = Field(default=0, ge=0)
    near_duplicate_algorithm: str = NEAR_DUPLICATE_ALGORITHM
    near_duplicate_algorithm_version: str = NEAR_DUPLICATE_ALGORITHM_VERSION
    near_duplicate_threshold: float = Field(
        default=DEFAULT_NEAR_DUPLICATE_THRESHOLD,
        ge=0,
        le=1,
    )
    duplicate_case_ids: tuple[str, ...] = ()
    exact_text_duplicate_case_ids: tuple[str, ...] = ()
    normalized_text_duplicate_case_ids: tuple[str, ...] = ()
    punctuationless_duplicate_case_ids: tuple[str, ...] = ()
    joined_turn_duplicate_case_ids: tuple[str, ...] = ()
    known_fingerprint_duplicate_case_ids: tuple[str, ...] = ()
    internal_duplicate_case_ids: tuple[str, ...] = ()
    near_duplicates: tuple[NearDuplicateMatch, ...] = ()

    @property
    def isolated(self) -> bool:
        return not (
            self.duplicate_case_ids
            or self.exact_text_duplicate_case_ids
            or self.normalized_text_duplicate_case_ids
            or self.punctuationless_duplicate_case_ids
            or self.joined_turn_duplicate_case_ids
            or self.known_fingerprint_duplicate_case_ids
            or self.internal_duplicate_case_ids
            or self.near_duplicates
        )


class ApprovalIdentityBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code_commit: str = Field(pattern=COMMIT_PATTERN)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    schema_sha256: str = Field(pattern=SHA256_PATTERN)
    scorer_sha256: str = Field(pattern=SHA256_PATTERN)
    gate_sha256: str = Field(pattern=SHA256_PATTERN)


class PendingApprovalPacket(BaseModel):
    """Machine-readable material only; this is deliberately not an approval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_schema_version: Literal["holdout-approval-v1"] = "holdout-approval-v1"
    status: Literal["PENDING_APPROVAL"] = "PENDING_APPROVAL"
    structured_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    grounded_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    identity: ApprovalIdentityBundle
    generated_at: datetime
    decision: None = None
    approver: None = None


class HoldoutApprovalRecord(BaseModel):
    """Independent human decision consumed by the executor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_schema_version: Literal["holdout-approval-v1"] = "holdout-approval-v1"
    approval_id: str = Field(min_length=1, max_length=200)
    approver: str = Field(min_length=1, max_length=200)
    approval_timestamp: datetime
    structured_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    grounded_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    identity: ApprovalIdentityBundle
    golden_review_confirmed: bool
    decision: ApprovalDecision
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_timestamp(self) -> HoldoutApprovalRecord:
        if self.approval_timestamp.tzinfo is None:
            raise ValueError("approval_timestamp must be timezone-aware")
        return self


class QualificationExecutionEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    online_credentials_present: bool
    default_provider: str
    business_traffic_routed_to_online_provider: bool


class QualificationResultRecord(BaseModel):
    """Safe aggregate-only, append-only result identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    qualification_run_id: str = Field(min_length=1, max_length=200)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    approval_sha256: str = Field(pattern=SHA256_PATTERN)
    code_commit: str = Field(pattern=COMMIT_PATTERN)
    started_at: datetime
    ended_at: datetime
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    scorer_version: str
    gate_version: str
    aggregate_metrics: Mapping[str, float | int | str | bool]
    routing_statistics: Mapping[str, int]
    latency_statistics: Mapping[str, float]
    error_classification: Mapping[str, int]
    gate_result: str
    status: Literal["COMPLETED", "ABORTED"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def manifest_sha256(manifest: HoldoutManifest) -> str:
    return sha256_value(manifest.model_dump(mode="json"))


def combined_identity(
    structured: HoldoutManifest,
    grounded: HoldoutManifest,
) -> ApprovalIdentityBundle:
    if structured.identity.code_commit != grounded.identity.code_commit:
        raise ValueError("suite code commits do not match")
    return ApprovalIdentityBundle(
        code_commit=structured.identity.code_commit,
        prompt_sha256=sha256_value(
            (structured.identity.prompt_sha256, grounded.identity.prompt_sha256)
        ),
        schema_sha256=sha256_value(
            (structured.identity.schema_sha256, grounded.identity.schema_sha256)
        ),
        scorer_sha256=sha256_value(
            (structured.identity.scorer_sha256, grounded.identity.scorer_sha256)
        ),
        gate_sha256=sha256_value((structured.identity.gate_sha256, grounded.identity.gate_sha256)),
    )


def create_pending_approval_packet(
    structured: HoldoutManifest,
    grounded: HoldoutManifest,
    *,
    generated_at: datetime | None = None,
) -> PendingApprovalPacket:
    if {structured.status, grounded.status} != {HoldoutStatus.SEALED}:
        raise ValueError("both Holdout suites must be sealed")
    return PendingApprovalPacket(
        structured_manifest_sha256=manifest_sha256(structured),
        grounded_manifest_sha256=manifest_sha256(grounded),
        identity=combined_identity(structured, grounded),
        generated_at=generated_at or datetime.now(UTC),
    )


def create_draft_manifest(
    *,
    dataset_path: Path,
    golden_path: Path,
    dataset_id: str,
    dataset_version: str,
    single_turn_count: int,
    multi_turn_count: int,
    category_distribution: Mapping[str, int],
    identity: FrozenQualificationIdentity,
    golden_review_status: GoldenReviewStatus = (
        GoldenReviewStatus.AUTOMATED_CHECKED_REVIEW_PENDING
    ),
    now: datetime | None = None,
) -> HoldoutManifest:
    cases = _read_jsonl(dataset_path)
    golden = _read_jsonl(golden_path)
    if _values(cases, "case_id") != _values(golden, "case_id"):
        raise ValueError("Holdout dataset and Golden Case IDs must match")
    return HoldoutManifest(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        case_count=len(cases),
        single_turn_count=single_turn_count,
        multi_turn_count=multi_turn_count,
        category_distribution=tuple(
            DistributionCount(label=label, count=count)
            for label, count in sorted(category_distribution.items())
        ),
        dataset_sha256=sha256_file(dataset_path),
        golden_sha256=sha256_file(golden_path),
        identity=identity,
        golden_review_status=golden_review_status,
        created_at=now or datetime.now(UTC),
    )


def seal_manifest(
    manifest: HoldoutManifest,
    *,
    dataset_path: Path,
    golden_path: Path,
    duplicates: DuplicateScanResult,
    now: datetime | None = None,
) -> HoldoutManifest:
    if manifest.status is not HoldoutStatus.DRAFT:
        raise ValueError("only a draft Holdout can be sealed")
    _verify_content_identity(manifest, dataset_path, golden_path)
    if not duplicates.isolated:
        raise ValueError("Holdout duplicates known or candidate cases")
    return manifest.model_copy(
        update={"status": HoldoutStatus.SEALED, "sealed_at": now or datetime.now(UTC)}
    )


def apply_independent_approval(
    manifest: HoldoutManifest,
    *,
    structured_manifest: HoldoutManifest,
    grounded_manifest: HoldoutManifest,
    approval: HoldoutApprovalRecord,
) -> HoldoutManifest:
    if manifest.status is not HoldoutStatus.SEALED:
        raise ValueError("only a sealed Holdout can be approved")
    _verify_approval(structured_manifest, grounded_manifest, approval)
    if approval.decision is not ApprovalDecision.APPROVED:
        raise ValueError("Holdout approval decision is not APPROVED")
    if not approval.golden_review_confirmed:
        raise ValueError("independent Golden review was not confirmed")
    return manifest.model_copy(
        update={
            "status": HoldoutStatus.APPROVED,
            "approved_at": approval.approval_timestamp,
            "approved_by": approval.approver,
            "golden_review_status": GoldenReviewStatus.INDEPENDENT_REVIEWED,
        }
    )


def consume_for_first_live_call(
    manifest: HoldoutManifest,
    *,
    dataset_path: Path,
    golden_path: Path,
    now: datetime | None = None,
) -> HoldoutManifest:
    if manifest.status is not HoldoutStatus.APPROVED:
        raise ValueError("only an approved Holdout can begin qualification")
    _verify_content_identity(manifest, dataset_path, golden_path)
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
    golden_path: Path,
    identity: FrozenQualificationIdentity,
) -> HoldoutManifest:
    changed = (
        sha256_file(dataset_path) != manifest.dataset_sha256
        or sha256_file(golden_path) != manifest.golden_sha256
        or identity != manifest.identity
    )
    return (
        manifest.model_copy(update={"status": HoldoutStatus.INVALIDATED}) if changed else manifest
    )


def scan_duplicates(
    candidate_path: Path,
    *,
    reference_paths: Iterable[Path],
    reference_text_paths: Iterable[Path] = (),
    known_fingerprints: Iterable[str] = (),
    near_duplicate_threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
) -> DuplicateScanResult:
    """Compare full candidate conversations against every known evidence source.

    Reports contain Case IDs and source paths only, never Holdout text.
    """

    candidate = _read_jsonl(candidate_path)
    candidate_units = _case_text_units(candidate, source=candidate_path)
    reference_units = tuple(
        unit
        for path in reference_paths
        for unit in _case_text_units(_read_jsonl(path), source=path)
    ) + tuple(unit for path in reference_text_paths for unit in _plain_text_units(path))
    candidate_ids = _values(candidate, "case_id")
    reference_ids = {unit.case_id for unit in reference_units if unit.case_id is not None}
    known = set(known_fingerprints)

    exact_reference = {unit.text for unit in reference_units}
    normalized_reference = {_normalize_text(unit.text) for unit in reference_units}
    punctuationless_reference = {_punctuationless(unit.text) for unit in reference_units}
    joined_reference = {_joined_turn_text(unit.text) for unit in reference_units}
    candidate_text_counts: dict[str, int] = {}
    for unit in candidate_units:
        normalized = _joined_turn_text(unit.text)
        candidate_text_counts[normalized] = candidate_text_counts.get(normalized, 0) + 1

    near_matches: list[NearDuplicateMatch] = []
    for candidate_index, candidate_unit in enumerate(candidate_units):
        candidate_normalized = _punctuationless(candidate_unit.text)
        for reference in reference_units:
            reference_normalized = _punctuationless(reference.text)
            if not candidate_normalized or not reference_normalized:
                continue
            if not _can_reach_similarity(
                candidate_normalized,
                reference_normalized,
                near_duplicate_threshold,
            ):
                continue
            similarity = SequenceMatcher(
                None,
                candidate_normalized,
                reference_normalized,
                autojunk=False,
            ).ratio()
            if similarity >= near_duplicate_threshold:
                near_matches.append(
                    NearDuplicateMatch(
                        candidate_case_id=candidate_unit.case_id or "UNKNOWN",
                        reference_source=reference.source,
                        reference_case_id=reference.case_id,
                        similarity=round(similarity, 6),
                    )
                )
        for other in candidate_units[candidate_index + 1 :]:
            other_normalized = _punctuationless(other.text)
            if not candidate_normalized or not other_normalized:
                continue
            if not _can_reach_similarity(
                candidate_normalized,
                other_normalized,
                near_duplicate_threshold,
            ):
                continue
            similarity = SequenceMatcher(
                None,
                candidate_normalized,
                other_normalized,
                autojunk=False,
            ).ratio()
            if similarity >= near_duplicate_threshold:
                near_matches.append(
                    NearDuplicateMatch(
                        candidate_case_id=candidate_unit.case_id or "UNKNOWN",
                        reference_source=str(candidate_path),
                        reference_case_id=other.case_id,
                        similarity=round(similarity, 6),
                    )
                )

    def matched_ids(predicate: Any) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    unit.case_id
                    for unit in candidate_units
                    if unit.case_id is not None and predicate(unit.text)
                }
            )
        )

    return DuplicateScanResult(
        candidate_case_count=len(candidate),
        reference_unit_count=len(reference_units),
        near_duplicate_threshold=near_duplicate_threshold,
        duplicate_case_ids=tuple(sorted(candidate_ids & reference_ids)),
        exact_text_duplicate_case_ids=matched_ids(lambda text: text in exact_reference),
        normalized_text_duplicate_case_ids=matched_ids(
            lambda text: _normalize_text(text) in normalized_reference
        ),
        punctuationless_duplicate_case_ids=matched_ids(
            lambda text: _punctuationless(text) in punctuationless_reference
        ),
        joined_turn_duplicate_case_ids=matched_ids(
            lambda text: _joined_turn_text(text) in joined_reference
        ),
        known_fingerprint_duplicate_case_ids=matched_ids(
            lambda text: (
                hashlib.sha256(_joined_turn_text(text).encode("utf-8")).hexdigest() in known
            )
        ),
        internal_duplicate_case_ids=tuple(
            sorted(
                unit.case_id
                for unit in candidate_units
                if unit.case_id is not None
                and candidate_text_counts[_joined_turn_text(unit.text)] > 1
            )
        ),
        near_duplicates=tuple(
            sorted(
                near_matches,
                key=lambda item: (
                    item.candidate_case_id,
                    item.reference_source,
                    item.reference_case_id or "",
                ),
            )
        ),
    )


def begin_first_live_call_atomically(
    *,
    target_manifest_path: Path,
    structured_manifest_path: Path,
    grounded_manifest_path: Path,
    approval_path: Path,
    dataset_path: Path,
    golden_path: Path,
    expected_identity: FrozenQualificationIdentity,
    environment: QualificationExecutionEnvironment,
    now: datetime | None = None,
) -> HoldoutManifest:
    """Acquire one execution right and persist CONSUMED before network dispatch."""

    lock_path = target_manifest_path.with_suffix(target_manifest_path.suffix + ".consume.lock")
    with _exclusive_lock(lock_path):
        target = _read_manifest(target_manifest_path)
        structured = _read_manifest(structured_manifest_path)
        grounded = _read_manifest(grounded_manifest_path)
        approval = HoldoutApprovalRecord.model_validate_json(
            approval_path.read_text(encoding="utf-8")
        )
        _verify_approval(structured, grounded, approval)
        _verify_execution_environment(environment)
        if target.identity != expected_identity:
            invalidated = target.model_copy(update={"status": HoldoutStatus.INVALIDATED})
            _atomic_write_model(target_manifest_path, invalidated)
            raise ValueError("Holdout frozen identity changed")
        consumed = consume_for_first_live_call(
            target,
            dataset_path=dataset_path,
            golden_path=golden_path,
            now=now,
        )
        _atomic_write_model(target_manifest_path, consumed)
        return consumed


def write_qualification_result_once(
    directory: Path,
    result: QualificationResultRecord,
) -> Path:
    """Create a unique aggregate-only result; an existing Run is immutable."""

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.qualification_run_id}.json"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, (result.model_dump_json(indent=2) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def append_audit_record(path: Path, record: BaseModel) -> None:
    """Append validated evidence; never overwrite an existing audit stream."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, (record.model_dump_json() + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_content_identity(
    manifest: HoldoutManifest,
    dataset_path: Path,
    golden_path: Path,
) -> None:
    if sha256_file(dataset_path) != manifest.dataset_sha256:
        raise ValueError("Holdout content changed after manifest creation")
    if sha256_file(golden_path) != manifest.golden_sha256:
        raise ValueError("Holdout Golden changed after manifest creation")
    cases = _read_jsonl(dataset_path)
    golden = _read_jsonl(golden_path)
    if len(cases) != manifest.case_count or len(golden) != manifest.case_count:
        raise ValueError("Holdout case count changed after manifest creation")
    if _values(cases, "case_id") != _values(golden, "case_id"):
        raise ValueError("Holdout dataset and Golden Case IDs no longer match")


def _verify_approval(
    structured: HoldoutManifest,
    grounded: HoldoutManifest,
    approval: HoldoutApprovalRecord,
) -> None:
    if approval.decision is not ApprovalDecision.APPROVED:
        raise ValueError("independent approval was not granted")
    checks = (
        approval.structured_manifest_sha256 == _sealed_manifest_sha256(structured),
        approval.grounded_manifest_sha256 == _sealed_manifest_sha256(grounded),
        approval.identity == combined_identity(structured, grounded),
    )
    if not all(checks):
        raise ValueError("approval does not match the sealed Holdout package")


def _sealed_manifest_sha256(manifest: HoldoutManifest) -> str:
    if manifest.sealed_at is None:
        raise ValueError("approval requires a sealed Holdout")
    sealed = manifest.model_copy(
        update={
            "status": HoldoutStatus.SEALED,
            "approved_at": None,
            "approved_by": None,
            "first_live_call_at": None,
            "live_call_count": 0,
            "golden_review_status": (GoldenReviewStatus.AUTOMATED_CHECKED_REVIEW_PENDING),
        }
    )
    return manifest_sha256(sealed)


def _verify_execution_environment(environment: QualificationExecutionEnvironment) -> None:
    if not environment.online_credentials_present:
        raise ValueError("online Provider credentials are unavailable")
    if environment.default_provider.casefold() != "scripted":
        raise ValueError("default business Provider must remain scripted")
    if environment.business_traffic_routed_to_online_provider:
        raise ValueError("business traffic is already routed to the online Provider")


def _read_manifest(path: Path) -> HoldoutManifest:
    return HoldoutManifest.model_validate_json(path.read_text(encoding="utf-8"))


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


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("Holdout qualification execution is already claimed") from exc
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode())
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


class _TextUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    case_id: str | None
    text: str


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
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


def _case_text_units(
    cases: Sequence[dict[str, Any]],
    *,
    source: Path,
) -> tuple[_TextUnit, ...]:
    units: list[_TextUnit] = []
    for case in cases:
        case_id = case.get("case_id")
        case_name = case_id if isinstance(case_id, str) else None
        text = _case_text(case)
        if not text:
            raise ValueError(f"case {case_name or 'UNKNOWN'} has no comparable text")
        units.append(_TextUnit(source=str(source), case_id=case_name, text=text))
    return tuple(units)


def _case_text(case: Mapping[str, Any]) -> str:
    comparison_text = case.get("comparison_text")
    if isinstance(comparison_text, str) and comparison_text:
        return comparison_text
    turns = case.get("conversation_turns")
    if isinstance(turns, list):
        values: list[str] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            content = turn.get("content")
            if isinstance(content, str):
                values.append(content)
        if values:
            return "\n".join(values)
    input_value = case.get("input")
    if isinstance(input_value, dict):
        message = input_value.get("current_user_message")
        if isinstance(message, str) and message:
            history = input_value.get("recent_conversation_messages")
            history_values: list[str] = []
            if isinstance(history, list):
                for item in history:
                    if not isinstance(item, dict):
                        continue
                    content = item.get("content")
                    if isinstance(content, str):
                        history_values.append(content)
            return "\n".join((*history_values, message))
        return json.dumps(input_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ""


def _plain_text_units(path: Path) -> tuple[_TextUnit, ...]:
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    units: list[_TextUnit] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if len(stripped) >= 8:
            units.append(
                _TextUnit(
                    source=f"{path}:{number}",
                    case_id=None,
                    text=stripped,
                )
            )
    return tuple(units)


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _punctuationless(value: str) -> str:
    normalized = _normalize_text(value)
    return "".join(
        character
        for character in normalized
        if not character.isspace() and not _is_punctuation(character)
    )


def _joined_turn_text(value: str) -> str:
    return "".join(_punctuationless(value).split())


def _is_punctuation(value: str) -> bool:
    return unicodedata.category(value).startswith(("P", "S"))


def _can_reach_similarity(left: str, right: str, threshold: float) -> bool:
    """Use SequenceMatcher's length upper bound without dropping valid matches."""

    return (2 * min(len(left), len(right)) / (len(left) + len(right))) >= threshold


__all__ = [
    "ApprovalDecision",
    "ApprovalIdentityBundle",
    "DEFAULT_NEAR_DUPLICATE_THRESHOLD",
    "DistributionCount",
    "DuplicateScanResult",
    "FrozenQualificationIdentity",
    "GoldenReviewStatus",
    "HoldoutApprovalRecord",
    "HoldoutManifest",
    "HoldoutStatus",
    "NearDuplicateMatch",
    "PendingApprovalPacket",
    "QualificationExecutionEnvironment",
    "QualificationResultRecord",
    "append_audit_record",
    "apply_independent_approval",
    "begin_first_live_call_atomically",
    "combined_identity",
    "consume_for_first_live_call",
    "create_draft_manifest",
    "create_pending_approval_packet",
    "invalidate_if_changed",
    "manifest_sha256",
    "record_consumed_call",
    "scan_duplicates",
    "seal_manifest",
    "sha256_file",
    "sha256_value",
    "write_qualification_result_once",
]
