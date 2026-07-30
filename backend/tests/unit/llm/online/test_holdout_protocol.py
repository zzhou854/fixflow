from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.llm.online.holdout_protocol import (
    HoldoutStatus,
    append_audit_record,
    consume_for_first_live_call,
    create_draft_manifest,
    invalidate_if_changed,
    record_consumed_call,
    scan_duplicates,
    seal_manifest,
)


def _dataset(path: Path, *rows: str) -> None:
    path.write_text(
        "".join(
            f'{{"case_id":"case-{index}","input":{{"current_user_message":"{text}"}}}}\n'
            for index, text in enumerate(rows, start=1)
        ),
        encoding="utf-8",
    )


def test_holdout_is_sealed_before_first_call_and_consumed_only_once(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.jsonl"
    reference = tmp_path / "reference.jsonl"
    _dataset(candidate, "厨房水管滴水", "门锁打不开")
    reference.write_text(
        '{"case_id":"reference-1","input":{"current_user_message":"客厅插座损坏"}}\n',
        encoding="utf-8",
    )
    duplicates = scan_duplicates(candidate, reference_paths=(reference,))
    draft = create_draft_manifest(
        dataset_path=candidate,
        dataset_id="resident_interpretation_holdout",
        dataset_version="2.0.0",
        scorer_version="1.1.0",
        gate_version="1.0.0",
        prompt_version="2.0.0",
        schema_version="resident-facts-v2",
        now=datetime(2026, 7, 30, tzinfo=UTC),
    )
    sealed = seal_manifest(
        draft,
        dataset_path=candidate,
        duplicates=duplicates,
        now=datetime(2026, 7, 30, 1, tzinfo=UTC),
    )
    consumed = consume_for_first_live_call(
        sealed,
        dataset_path=candidate,
        now=datetime(2026, 7, 30, 2, tzinfo=UTC),
    )

    assert draft.status is HoldoutStatus.DRAFT
    assert sealed.status is HoldoutStatus.SEALED
    assert consumed.status is HoldoutStatus.CONSUMED
    assert consumed.live_call_count == 1
    assert record_consumed_call(consumed).live_call_count == 2
    with pytest.raises(ValueError, match="only a sealed"):
        consume_for_first_live_call(consumed, dataset_path=candidate)


def test_duplicate_text_id_and_known_fingerprint_block_sealing(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.jsonl"
    reference = tmp_path / "reference.jsonl"
    _dataset(candidate, " 厨房  漏水 ")
    _dataset(reference, "厨房漏水")
    duplicates = scan_duplicates(candidate, reference_paths=(reference,))
    draft = create_draft_manifest(
        dataset_path=candidate,
        dataset_id="future_holdout",
        dataset_version="1.0.0",
        scorer_version="1",
        gate_version="1",
        prompt_version="1",
        schema_version="1",
    )
    assert duplicates.normalized_text_duplicates
    assert duplicates.duplicate_case_ids == ("case-1",)
    with pytest.raises(ValueError, match="duplicates"):
        seal_manifest(draft, dataset_path=candidate, duplicates=duplicates)


def test_changed_content_or_identity_invalidates_manifest(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.jsonl"
    _dataset(candidate, "厨房漏水")
    manifest = create_draft_manifest(
        dataset_path=candidate,
        dataset_id="future_holdout",
        dataset_version="1.0.0",
        scorer_version="1",
        gate_version="1",
        prompt_version="1",
        schema_version="1",
    )
    changed = invalidate_if_changed(
        manifest,
        dataset_path=candidate,
        scorer_version="2",
        gate_version="1",
        prompt_version="1",
        schema_version="1",
    )
    assert changed.status is HoldoutStatus.INVALIDATED


def test_audit_records_are_appended_not_overwritten(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.jsonl"
    _dataset(candidate, "厨房漏水")
    manifest = create_draft_manifest(
        dataset_path=candidate,
        dataset_id="future_holdout",
        dataset_version="1.0.0",
        scorer_version="1",
        gate_version="1",
        prompt_version="1",
        schema_version="1",
    )
    audit = tmp_path / "audit.jsonl"
    append_audit_record(audit, manifest)
    append_audit_record(audit, manifest)
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 2
