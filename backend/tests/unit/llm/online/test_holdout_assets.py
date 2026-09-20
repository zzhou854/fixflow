from __future__ import annotations

import json
from pathlib import Path

from app.llm.online.holdout_protocol import (
    HoldoutManifest,
    HoldoutStatus,
    PendingApprovalPacket,
    create_pending_approval_packet,
    sha256_file,
)

ROOT = Path(__file__).parents[5]
ASSETS = ROOT / "backend" / "evals" / "holdout"
MANIFESTS = ASSETS / "manifests"


def test_both_holdout_suites_are_independently_sealed_without_live_calls() -> None:
    structured = HoldoutManifest.model_validate_json(
        (MANIFESTS / "resident_interpretation_holdout_v2.manifest.json").read_text(encoding="utf-8")
    )
    grounded = HoldoutManifest.model_validate_json(
        (MANIFESTS / "grounded_response_holdout_v1.manifest.json").read_text(encoding="utf-8")
    )

    assert structured.dataset_id == "resident_interpretation_holdout"
    assert structured.dataset_version == "2.0.0"
    assert structured.case_count == 180
    assert structured.single_turn_count == 120
    assert structured.multi_turn_count == 60
    assert {item.label: item.count for item in structured.category_distribution} == {
        "DOOR_LOCK": 60,
        "ELECTRICAL": 60,
        "WATER_LEAK": 60,
    }
    assert grounded.dataset_id == "grounded_response_holdout"
    assert grounded.dataset_version == "1.0.0"
    assert grounded.case_count == 60
    assert grounded.single_turn_count == 60
    assert grounded.multi_turn_count == 0
    for manifest in (structured, grounded):
        assert manifest.status is HoldoutStatus.SEALED
        assert manifest.live_call_count == 0
        assert manifest.first_live_call_at is None
        assert manifest.approved_at is None
        assert manifest.approved_by is None
        assert manifest.identity.code_commit == ("63b78e08ad97cab40314e1f3b34e1f6b8671e5ba")


def test_pending_approval_material_cannot_authorize_execution() -> None:
    packet = PendingApprovalPacket.model_validate_json(
        (ASSETS / "approval" / "holdout_approval.pending.json").read_text(encoding="utf-8")
    )

    assert packet.status == "PENDING_APPROVAL"
    assert packet.decision is None
    assert packet.approver is None


def test_manifest_hashes_bind_frozen_scorers_and_gates() -> None:
    structured = HoldoutManifest.model_validate_json(
        (MANIFESTS / "resident_interpretation_holdout_v2.manifest.json").read_text(encoding="utf-8")
    )
    grounded = HoldoutManifest.model_validate_json(
        (MANIFESTS / "grounded_response_holdout_v1.manifest.json").read_text(encoding="utf-8")
    )

    assert structured.identity.scorer_sha256 == sha256_file(ASSETS / "structured_scorer_v1.json")
    assert structured.identity.gate_sha256 == sha256_file(ASSETS / "structured_gate_v1.json")
    assert grounded.identity.scorer_sha256 == sha256_file(ASSETS / "grounded_scorer_v1.json")
    assert grounded.identity.gate_sha256 == sha256_file(ASSETS / "grounded_gate_v1.json")


def test_repository_holdout_assets_contain_no_case_or_golden_plaintext() -> None:
    forbidden_keys = {
        "conversation_turns",
        "current_user_message",
        "expected_evidence_facts",
        "adjudication_notes",
        "safe_text",
        "raw_response",
        "authorization",
        "api_key",
    }
    for path in ASSETS.rglob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        serialized = json.dumps(value, ensure_ascii=False).casefold()
        for key in forbidden_keys:
            assert f'"{key.casefold()}"' not in serialized, path


def test_holdout_directory_is_outside_git_and_docker_build_context() -> None:
    repository = ROOT.resolve()
    external = (ROOT.parent / "fixflow-holdouts").resolve()

    assert repository not in external.parents
    assert external not in repository.parents


def test_revised_holdout_suites_are_sealed_and_require_fresh_review() -> None:
    structured = HoldoutManifest.model_validate_json(
        (MANIFESTS / "resident_interpretation_holdout_v2_1.manifest.json").read_text(
            encoding="utf-8"
        )
    )
    grounded = HoldoutManifest.model_validate_json(
        (MANIFESTS / "grounded_response_holdout_v1_1.manifest.json").read_text(encoding="utf-8")
    )
    packet = PendingApprovalPacket.model_validate_json(
        (ASSETS / "approval" / "holdout_approval.revised.pending.json").read_text(encoding="utf-8")
    )

    assert structured.dataset_version == "2.1.0"
    assert structured.case_count == 180
    assert structured.single_turn_count == 120
    assert structured.multi_turn_count == 60
    assert grounded.dataset_version == "1.1.0"
    assert grounded.case_count == 60
    assert grounded.single_turn_count == 60
    assert grounded.multi_turn_count == 0
    assert {item.label for item in grounded.category_distribution} >= {
        "TICKET_CANCELLED",
        "TICKET_CLOSED",
        "APPOINTMENT_CANCELLED",
        "TICKET_CREATION_FAILED",
        "APPOINTMENT_PENDING",
        "HUMAN_REVIEW_CREATED",
        "POLICY_REVIEW_REQUIRED",
    }
    for manifest in (structured, grounded):
        assert manifest.status is HoldoutStatus.SEALED
        assert manifest.live_call_count == 0
        assert manifest.first_live_call_at is None
        assert manifest.approved_at is None
        assert manifest.approved_by is None
        assert manifest.identity.code_commit == ("990bbfcbf0c03e40c4b339be71b959a0065a30ba")
    assert packet == create_pending_approval_packet(
        structured,
        grounded,
        generated_at=packet.generated_at,
    )
    assert packet.status == "PENDING_APPROVAL"
    assert packet.decision is None
    assert packet.approver is None


def test_revised_manifest_hashes_bind_new_scorers_and_gates() -> None:
    structured = HoldoutManifest.model_validate_json(
        (MANIFESTS / "resident_interpretation_holdout_v2_1.manifest.json").read_text(
            encoding="utf-8"
        )
    )
    grounded = HoldoutManifest.model_validate_json(
        (MANIFESTS / "grounded_response_holdout_v1_1.manifest.json").read_text(encoding="utf-8")
    )

    assert structured.identity.scorer_version.endswith("@1.1.0")
    assert structured.identity.scorer_sha256 == sha256_file(ASSETS / "structured_scorer_v1_1.json")
    assert structured.identity.gate_version.endswith("@1.1.0")
    assert structured.identity.gate_sha256 == sha256_file(ASSETS / "structured_gate_v1_1.json")
    assert grounded.identity.scorer_version.endswith("@1.1.0")
    assert grounded.identity.scorer_sha256 == sha256_file(ASSETS / "grounded_scorer_v1_1.json")
    assert grounded.identity.gate_version.endswith("@1.1.0")
    assert grounded.identity.gate_sha256 == sha256_file(ASSETS / "grounded_gate_v1_1.json")


def test_revised_preparation_report_records_no_live_or_online_calls() -> None:
    summary = json.loads(
        (ASSETS / "reports" / "preparation_summary.revised.json").read_text(encoding="utf-8")
    )
    duplicate_report = json.loads(
        (ASSETS / "reports" / "duplicate_scan.revised.json").read_text(encoding="utf-8")
    )

    assert summary["status"] == ("REVISED_PACKAGES_PREPARED_PENDING_INDEPENDENT_REVIEW")
    assert summary["review_issue_count"] == 85
    assert summary["resolved_issue_count"] == 85
    assert summary["structured_automated_check_count"] == 180
    assert summary["grounded_automated_check_count"] == 60
    assert summary["manifest_status"] == "SEALED"
    assert summary["approval_status"] == "PENDING_APPROVAL"
    assert summary["live_call_count"] == 0
    assert summary["online_call_count"] == 0
    assert summary["independent_review_status"] == "NOT_STARTED"
    for suite in ("structured", "grounded"):
        assert not duplicate_report[suite]["duplicate_case_ids"]
        assert not duplicate_report[suite]["internal_duplicate_case_ids"]
        assert not duplicate_report[suite]["near_duplicates"]


def test_internal_assisted_review_is_advisory_and_call_free() -> None:
    report = json.loads(
        (ASSETS / "reports" / "internal_assisted_review.revised.json").read_text(encoding="utf-8")
    )

    assert report["status"] == "ASSISTED_COMPLETE_AWAITING_INDEPENDENT_HUMAN_REVIEW"
    assert report["review_kind"] == "INTERNAL_ADVERSARIAL_ASSISTED_NOT_INDEPENDENT"
    assert report["status_counts"] == {
        "ASSISTED_ACCEPT": 240,
        "ASSISTED_ISSUE": 0,
        "ASSISTED_UNCERTAIN": 0,
    }
    assert report["unresolved_severity_counts"] == {
        "BLOCKER": 0,
        "MAJOR": 0,
        "MINOR": 0,
    }
    assert report["independent_human_review_completed"] is False
    assert report["approval_decision"] is None
    assert report["online_provider_calls"] == 0


def test_frozen_qualification_runtime_matches_sealed_identity() -> None:
    report = json.loads(
        (ASSETS / "reports" / "qualification_runtime.revised.json").read_text(encoding="utf-8")
    )

    assert report["runtime_commit"] == "990bbfcbf0c03e40c4b339be71b959a0065a30ba"
    assert report["runtime_tag"] == ("qualification-runtime-structured-2.1.0-grounded-1.1.0")
    assert report["structured_identity_matches_manifest"] is True
    assert report["grounded_identity_matches_manifest"] is True
    assert report["worktree_clean"] is True
    assert report["default_provider"] == "scripted"
    assert report["online_provider_status"] == "NOT_ACTIVATED"
    assert report["formal_holdout_live_call_count"] == 0
    assert report["formal_shadow_status"] == "NOT_EXECUTED"
    assert report["canary_status"] == "NOT_EXECUTED"
    assert report["approval"] == "PENDING_HUMAN_SIGNATURE"


def test_original_manifest_content_identities_remain_frozen() -> None:
    structured = HoldoutManifest.model_validate_json(
        (MANIFESTS / "resident_interpretation_holdout_v2.manifest.json").read_text(encoding="utf-8")
    )
    grounded = HoldoutManifest.model_validate_json(
        (MANIFESTS / "grounded_response_holdout_v1.manifest.json").read_text(encoding="utf-8")
    )

    assert structured.dataset_sha256 == (
        "ea7f4efdc5994e1c11444999c4171306caea98c431b41df08991acbc592a8c07"
    )
    assert structured.golden_sha256 == (
        "cf93a02c8013ab66d061ff56de5003a5bba8d0f93c69b244284f5a822b789822"
    )
    assert grounded.dataset_sha256 == (
        "a11949a6db8168398d6c7eb06882eed59da32b3e09a9b4d5ec8121527008c146"
    )
    assert grounded.golden_sha256 == (
        "9fb6104833c473ef727a866ddbaf6cee3a75e9b2dfcd2cb58eda8a86c9037a83"
    )


def test_v2_external_review_suites_are_sealed_without_execution() -> None:
    structured = HoldoutManifest.model_validate_json(
        (MANIFESTS / "resident_interpretation_holdout_v2_2.manifest.json").read_text(
            encoding="utf-8"
        )
    )
    grounded = HoldoutManifest.model_validate_json(
        (MANIFESTS / "grounded_response_holdout_v1_2.manifest.json").read_text(encoding="utf-8")
    )
    approval = json.loads(
        (ASSETS / "approval" / "holdout_approval.v2.pending.json").read_text(encoding="utf-8")
    )

    assert structured.dataset_version == "2.2.0"
    assert structured.case_count == 180
    assert structured.single_turn_count + structured.multi_turn_count == 180
    assert structured.dataset_sha256 == (
        "5597454685b72a8518b949c940ada88b212c586314f97dfee23d72e5604a5328"
    )
    assert structured.golden_sha256 == (
        "f7ac762c4bb3e0f5253bc1f4c25f3cffab2a7b637b036710a468dd6fb1da0a3f"
    )
    assert grounded.dataset_version == "1.2.0"
    assert grounded.case_count == 90
    assert grounded.single_turn_count == 90
    assert grounded.multi_turn_count == 0
    assert grounded.dataset_sha256 == (
        "e3949114ededb238daa831495fa7a865b22984ef25a219004650341bbaa07e7a"
    )
    assert grounded.golden_sha256 == (
        "6487b22200a7c72e7f68a8e92389fa2b3514d3baa25aeb69a03dc977aec630ef"
    )
    for manifest in (structured, grounded):
        assert manifest.status is HoldoutStatus.SEALED
        assert manifest.live_call_count == 0
        assert manifest.first_live_call_at is None
        assert manifest.approved_at is None
        assert manifest.approved_by is None
        assert manifest.identity.code_commit == ("35e58526d5e10c42c080a20090166af2d13861b8")
    assert structured.identity.scorer_version.endswith("@1.1.0")
    assert structured.identity.gate_version.endswith("@1.1.0")
    assert grounded.identity.scorer_version.endswith("@1.2.0")
    assert grounded.identity.gate_version.endswith("@1.2.0")
    assert approval["status"] == "PENDING_HUMAN_SIGNATURE"
    assert approval["signature"] is None
    assert approval["approver"] is None
    assert approval["decision"] is None


def test_v2_preparation_report_is_review_only() -> None:
    report = json.loads(
        (ASSETS / "reports" / "preparation_summary.v2.json").read_text(encoding="utf-8")
    )

    assert report["status"] == "REVISED_HOLDOUT_SEALED_AWAITING_EXTERNAL_REVIEW"
    assert report["structured_case_count"] == 180
    assert report["grounded_case_count"] == 90
    assert report["grounded_deterministic_count"] == 60
    assert report["grounded_natural_count"] == 30
    assert report["duplicate_scan"] == "PASS"
    assert report["approval"] == "PENDING_HUMAN_SIGNATURE"
    assert report["signature"] is None
    assert report["live_call_count"] == 0
    assert report["qualification_execution_count"] == 0
