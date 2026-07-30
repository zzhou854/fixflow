from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.llm.online.holdout_protocol import (
    ApprovalDecision,
    FrozenQualificationIdentity,
    GoldenReviewStatus,
    HoldoutApprovalRecord,
    HoldoutManifest,
    HoldoutStatus,
    PendingApprovalPacket,
    QualificationExecutionEnvironment,
    QualificationResultRecord,
    apply_independent_approval,
    begin_first_live_call_atomically,
    combined_identity,
    consume_for_first_live_call,
    create_draft_manifest,
    create_pending_approval_packet,
    invalidate_if_changed,
    manifest_sha256,
    record_consumed_call,
    scan_duplicates,
    seal_manifest,
    write_qualification_result_once,
)

NOW = datetime(2026, 7, 30, tzinfo=UTC)


def _jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _suite_files(path: Path, prefix: str, texts: tuple[str, ...]) -> tuple[Path, Path]:
    dataset = path / f"{prefix}-dataset.jsonl"
    golden = path / f"{prefix}-golden.jsonl"
    _jsonl(
        dataset,
        [
            {
                "case_id": f"{prefix}-{index}",
                "conversation_turns": [{"role": "USER", "content": text}],
            }
            for index, text in enumerate(texts, start=1)
        ],
    )
    _jsonl(
        golden,
        [
            {"case_id": f"{prefix}-{index}", "expected_intent": "NEW_REPAIR"}
            for index, _ in enumerate(texts, start=1)
        ],
    )
    return dataset, golden


def _identity(seed: str = "a") -> FrozenQualificationIdentity:
    return FrozenQualificationIdentity(
        code_commit=seed * 40,
        prompt_version="2.0.0",
        prompt_sha256=seed * 64,
        schema_version="resident-facts-v2",
        schema_sha256=seed * 64,
        normalization_version="hybrid-normalizer-v1",
        normalization_sha256=seed * 64,
        scorer_version="1.0.0",
        scorer_sha256=seed * 64,
        gate_version="1.0.0",
        gate_sha256=seed * 64,
        provider_router_version="flash-pro-router-v1",
    )


def _draft(path: Path, prefix: str, texts: tuple[str, ...]) -> tuple[HoldoutManifest, Path, Path]:
    dataset, golden = _suite_files(path, prefix, texts)
    return (
        create_draft_manifest(
            dataset_path=dataset,
            golden_path=golden,
            dataset_id=prefix,
            dataset_version="1.0.0",
            single_turn_count=len(texts),
            multi_turn_count=0,
            category_distribution={"ALL": len(texts)},
            identity=_identity(),
            now=NOW,
        ),
        dataset,
        golden,
    )


def _sealed(
    path: Path,
    prefix: str,
    texts: tuple[str, ...],
) -> tuple[HoldoutManifest, Path, Path]:
    draft, dataset, golden = _draft(path, prefix, texts)
    reference, _ = _suite_files(path, f"{prefix}-reference", ("完全不同的参考内容",))
    duplicates = scan_duplicates(
        dataset,
        reference_paths=(reference,),
        near_duplicate_threshold=0.99,
    )
    return (
        seal_manifest(
            draft,
            dataset_path=dataset,
            golden_path=golden,
            duplicates=duplicates,
            now=NOW,
        ),
        dataset,
        golden,
    )


def _approval(
    structured: HoldoutManifest,
    grounded: HoldoutManifest,
) -> HoldoutApprovalRecord:
    pending = create_pending_approval_packet(
        structured,
        grounded,
        generated_at=NOW,
    )
    return HoldoutApprovalRecord(
        approval_id="human-approval-1",
        approver="independent-reviewer",
        approval_timestamp=NOW,
        structured_manifest_sha256=pending.structured_manifest_sha256,
        grounded_manifest_sha256=pending.grounded_manifest_sha256,
        identity=pending.identity,
        golden_review_confirmed=True,
        decision=ApprovalDecision.APPROVED,
        notes="Independent review completed.",
    )


def test_draft_seals_but_cannot_execute_without_independent_approval(
    tmp_path: Path,
) -> None:
    sealed, dataset, golden = _sealed(tmp_path, "future-holdout", ("厨房水管滴水",))

    assert sealed.status is HoldoutStatus.SEALED
    assert sealed.golden_review_status is GoldenReviewStatus.AUTOMATED_CHECKED_REVIEW_PENDING
    with pytest.raises(ValueError, match="only an approved"):
        consume_for_first_live_call(
            sealed,
            dataset_path=dataset,
            golden_path=golden,
        )


def test_pending_packet_is_not_an_approval_and_cannot_name_an_approver(
    tmp_path: Path,
) -> None:
    structured, _, _ = _sealed(tmp_path, "structured", ("卫生间水管渗水",))
    grounded, _, _ = _sealed(tmp_path, "grounded", ("结构化响应场景",))

    packet = create_pending_approval_packet(structured, grounded, generated_at=NOW)

    assert isinstance(packet, PendingApprovalPacket)
    assert packet.status == "PENDING_APPROVAL"
    assert packet.decision is None
    assert packet.approver is None


def test_independent_approval_binds_both_manifests_and_all_frozen_identities(
    tmp_path: Path,
) -> None:
    structured, _, _ = _sealed(tmp_path, "structured", ("卫生间下水管渗水",))
    grounded, _, _ = _sealed(tmp_path, "grounded", ("返回工单创建结果",))
    approval = _approval(structured, grounded)

    approved = apply_independent_approval(
        structured,
        structured_manifest=structured,
        grounded_manifest=grounded,
        approval=approval,
    )

    assert approved.status is HoldoutStatus.APPROVED
    assert approved.approved_by == "independent-reviewer"
    assert approved.golden_review_status is GoldenReviewStatus.INDEPENDENT_REVIEWED

    wrong = approval.model_copy(update={"structured_manifest_sha256": "b" * 64})
    with pytest.raises(ValueError, match="does not match"):
        apply_independent_approval(
            structured,
            structured_manifest=structured,
            grounded_manifest=grounded,
            approval=wrong,
        )


def test_rejected_approval_never_approves_a_suite(tmp_path: Path) -> None:
    structured, _, _ = _sealed(tmp_path, "structured", ("入户门锁舌卡住",))
    grounded, _, _ = _sealed(tmp_path, "grounded", ("返回人工处理结果",))
    rejected = _approval(structured, grounded).model_copy(
        update={"decision": ApprovalDecision.REJECTED}
    )

    with pytest.raises(ValueError, match="not granted"):
        apply_independent_approval(
            structured,
            structured_manifest=structured,
            grounded_manifest=grounded,
            approval=rejected,
        )


@pytest.mark.parametrize(
    "changed_field",
    [
        "prompt_sha256",
        "schema_sha256",
        "normalization_sha256",
        "scorer_sha256",
        "gate_sha256",
        "code_commit",
    ],
)
def test_every_frozen_identity_change_invalidates_manifest(
    tmp_path: Path,
    changed_field: str,
) -> None:
    manifest, dataset, golden = _draft(tmp_path, "future-holdout", ("阳台地漏渗水",))
    replacement = "b" * (40 if changed_field == "code_commit" else 64)
    identity = manifest.identity.model_copy(update={changed_field: replacement})

    changed = invalidate_if_changed(
        manifest,
        dataset_path=dataset,
        golden_path=golden,
        identity=identity,
    )

    assert changed.status is HoldoutStatus.INVALIDATED


def test_dataset_or_golden_change_invalidates_manifest(tmp_path: Path) -> None:
    manifest, dataset, golden = _draft(tmp_path, "future-holdout", ("客厅灯不亮",))
    dataset.write_text(dataset.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert (
        invalidate_if_changed(
            manifest,
            dataset_path=dataset,
            golden_path=golden,
            identity=manifest.identity,
        ).status
        is HoldoutStatus.INVALIDATED
    )


def test_duplicate_scan_covers_exact_normalized_punctuation_joined_and_near_text(
    tmp_path: Path,
) -> None:
    candidate, _ = _suite_files(tmp_path, "candidate", ("厨房  漏水！",))
    reference, _ = _suite_files(tmp_path, "reference", ("厨房漏水",))

    duplicates = scan_duplicates(
        candidate,
        reference_paths=(reference,),
        near_duplicate_threshold=0.8,
    )

    assert duplicates.normalized_text_duplicate_case_ids == ()
    assert duplicates.punctuationless_duplicate_case_ids == ("candidate-1",)
    assert duplicates.joined_turn_duplicate_case_ids == ("candidate-1",)
    assert duplicates.near_duplicates
    assert not duplicates.isolated


def test_first_live_call_is_atomically_consumed_and_concurrent_reuse_is_denied(
    tmp_path: Path,
) -> None:
    structured, structured_dataset, structured_golden = _sealed(
        tmp_path, "structured", ("厨房洗菜盆下面在滴水",)
    )
    grounded, _, _ = _sealed(tmp_path, "grounded", ("展示预约失败结果",))
    approval = _approval(structured, grounded)
    structured = apply_independent_approval(
        structured,
        structured_manifest=structured,
        grounded_manifest=grounded,
        approval=approval,
    )
    grounded = apply_independent_approval(
        grounded,
        structured_manifest=structured.model_copy(
            update={
                "status": HoldoutStatus.SEALED,
                "approved_at": None,
                "approved_by": None,
                "golden_review_status": (GoldenReviewStatus.AUTOMATED_CHECKED_REVIEW_PENDING),
            }
        ),
        grounded_manifest=grounded,
        approval=approval,
    )
    structured_manifest_path = tmp_path / "structured-manifest.json"
    grounded_manifest_path = tmp_path / "grounded-manifest.json"
    approval_path = tmp_path / "approval.json"
    structured_manifest_path.write_text(
        structured.model_dump_json(indent=2),
        encoding="utf-8",
    )
    grounded_manifest_path.write_text(
        grounded.model_dump_json(indent=2),
        encoding="utf-8",
    )
    approval_path.write_text(approval.model_dump_json(indent=2), encoding="utf-8")
    environment = QualificationExecutionEnvironment(
        online_credentials_present=True,
        default_provider="scripted",
        business_traffic_routed_to_online_provider=False,
    )

    def claim() -> str:
        try:
            return begin_first_live_call_atomically(
                target_manifest_path=structured_manifest_path,
                structured_manifest_path=structured_manifest_path,
                grounded_manifest_path=grounded_manifest_path,
                approval_path=approval_path,
                dataset_path=structured_dataset,
                golden_path=structured_golden,
                expected_identity=structured.identity,
                environment=environment,
                now=NOW,
            ).status.value
        except (RuntimeError, ValueError) as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _: claim(), range(2)))

    persisted = HoldoutManifest.model_validate_json(
        structured_manifest_path.read_text(encoding="utf-8")
    )
    assert outcomes.count("CONSUMED") == 1
    assert len(outcomes) == 2
    assert persisted.status is HoldoutStatus.CONSUMED
    assert persisted.live_call_count == 1
    assert record_consumed_call(persisted).live_call_count == 2


def test_execution_lock_rejects_non_scripted_default_or_business_routing(
    tmp_path: Path,
) -> None:
    structured, dataset, golden = _sealed(tmp_path, "structured", ("书房插座没电",))
    grounded, _, _ = _sealed(tmp_path, "grounded", ("展示权限拒绝",))
    approval = _approval(structured, grounded)
    structured = apply_independent_approval(
        structured,
        structured_manifest=structured,
        grounded_manifest=grounded,
        approval=approval,
    )
    grounded = apply_independent_approval(
        grounded,
        structured_manifest=structured.model_copy(
            update={
                "status": HoldoutStatus.SEALED,
                "approved_at": None,
                "approved_by": None,
                "golden_review_status": (GoldenReviewStatus.AUTOMATED_CHECKED_REVIEW_PENDING),
            }
        ),
        grounded_manifest=grounded,
        approval=approval,
    )
    structured_path = tmp_path / "s.json"
    grounded_path = tmp_path / "g.json"
    approval_path = tmp_path / "a.json"
    structured_path.write_text(structured.model_dump_json(), encoding="utf-8")
    grounded_path.write_text(grounded.model_dump_json(), encoding="utf-8")
    approval_path.write_text(approval.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError, match="scripted"):
        begin_first_live_call_atomically(
            target_manifest_path=structured_path,
            structured_manifest_path=structured_path,
            grounded_manifest_path=grounded_path,
            approval_path=approval_path,
            dataset_path=dataset,
            golden_path=golden,
            expected_identity=structured.identity,
            environment=QualificationExecutionEnvironment(
                online_credentials_present=True,
                default_provider="deepseek",
                business_traffic_routed_to_online_provider=False,
            ),
        )


def test_results_are_aggregate_only_and_cannot_be_overwritten(tmp_path: Path) -> None:
    result = QualificationResultRecord(
        qualification_run_id="run-1",
        manifest_sha256="a" * 64,
        approval_sha256="b" * 64,
        code_commit="c" * 40,
        started_at=NOW,
        ended_at=NOW,
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_version="2.0.0",
        schema_version="resident-facts-v2",
        scorer_version="1.0.0",
        gate_version="1.0.0",
        aggregate_metrics={"completion_rate": 1.0},
        routing_statistics={"flash_first_success": 180},
        latency_statistics={"p95_ms": 3000.0},
        error_classification={},
        gate_result="PASSED",
        status="COMPLETED",
    )
    path = write_qualification_result_once(tmp_path, result)
    assert path.exists()
    with pytest.raises(FileExistsError):
        write_qualification_result_once(tmp_path, result)


def test_manifest_hash_and_combined_identity_are_deterministic(tmp_path: Path) -> None:
    structured, _, _ = _sealed(tmp_path, "structured", ("玄关门把手松动",))
    grounded, _, _ = _sealed(tmp_path, "grounded", ("展示等待补充信息",))

    assert manifest_sha256(structured) == manifest_sha256(structured)
    assert combined_identity(structured, grounded) == combined_identity(structured, grounded)
