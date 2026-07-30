from __future__ import annotations

import json
from pathlib import Path

from app.llm.online.holdout_protocol import (
    HoldoutManifest,
    HoldoutStatus,
    PendingApprovalPacket,
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
