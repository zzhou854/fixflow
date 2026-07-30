from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.llm.online.holdout_identity import grounded_identity, structured_identity
from app.llm.online.holdout_preparation import (
    validate_grounded_assets,
    validate_structured_assets,
)
from app.llm.online.holdout_protocol import sha256_file


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_structured_assets_validate_two_pass_rules_and_distribution(
    tmp_path: Path,
) -> None:
    turns = [{"role": "USER", "content": "厨房洗菜盆下面正在滴水。"}]
    dataset = tmp_path / "dataset.jsonl"
    golden = tmp_path / "golden.jsonl"
    _write(
        dataset,
        [
            {
                "case_id": "structured-001",
                "category": "WATER_LEAK",
                "conversation_turns": turns,
            }
        ],
    )
    _write(
        golden,
        [
            {
                "case_id": "structured-001",
                "conversation_turns": turns,
                "expected_evidence_facts": [
                    {
                        "field": "issue_category",
                        "value": "WATER_LEAK",
                        "evidence": "滴水",
                    },
                    {
                        "field": "issue_location",
                        "value": "厨房",
                        "evidence": "厨房",
                    },
                ],
                "expected_null_fields": ["explicit_property_reference"],
                "expected_intent": "NEW_REPAIR",
                "expected_missing_fields": [],
                "expected_clarification": False,
                "expected_safety_class": "NONE",
                "expected_critical_safety": False,
                "expected_human_boundary": False,
                "expected_property_authorization_boundary": "VERIFIED",
                "allowed_variants": {},
                "adjudication_notes": "普通漏水报修，事实完整。",
            }
        ],
    )

    report, counts = validate_structured_assets(dataset, golden)

    assert report.schema_validation_passed
    assert report.rule_consistency_passed
    assert report.status == "REVIEW_PENDING"
    assert counts == {
        "single": 1,
        "multi": 0,
        "categories": {"WATER_LEAK": 1},
    }


def test_structured_assets_reject_unsupported_evidence_and_inconsistent_human_rule(
    tmp_path: Path,
) -> None:
    turns = [{"role": "USER", "content": "门把手松了。"}]
    dataset = tmp_path / "dataset.jsonl"
    golden = tmp_path / "golden.jsonl"
    _write(
        dataset,
        [{"case_id": "s-1", "category": "DOOR_LOCK", "conversation_turns": turns}],
    )
    _write(
        golden,
        [
            {
                "case_id": "s-1",
                "conversation_turns": turns,
                "expected_evidence_facts": [
                    {
                        "field": "issue_category",
                        "value": "DOOR_LOCK",
                        "evidence": "锁芯",
                    }
                ],
                "expected_null_fields": [],
                "expected_intent": "NEW_REPAIR",
                "expected_missing_fields": [],
                "expected_clarification": False,
                "expected_safety_class": "NONE",
                "expected_critical_safety": False,
                "expected_human_boundary": True,
                "expected_property_authorization_boundary": "VERIFIED",
                "allowed_variants": {},
                "adjudication_notes": "用于验证拒绝路径。",
            }
        ],
    )

    with pytest.raises(ValueError):
        validate_structured_assets(dataset, golden)


def test_grounded_assets_require_outcome_action_and_allowed_fact_identity(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset.jsonl"
    golden = tmp_path / "golden.jsonl"
    _write(
        dataset,
        [
            {
                "case_id": "g-1",
                "category": "TICKET_CREATED",
                "input": {
                    "message_outcome": "COMPLETED",
                    "required_user_action": "请选择上门时间",
                    "allowed_fact_ids": ["ticket.created"],
                },
            }
        ],
    )
    _write(
        golden,
        [
            {
                "case_id": "g-1",
                "required_information": ["工单已经创建"],
                "forbidden_information": ["已经预约成功"],
                "allowed_fact_ids": ["ticket.created"],
                "forbidden_claims": ["师傅已经出发"],
                "expected_message_outcome": "COMPLETED",
                "expected_required_user_action": "请选择上门时间",
                "expected_template_id": "TICKET_CREATED",
                "deterministic_template_required": True,
                "safety_template_required": False,
            }
        ],
    )

    report, counts = validate_grounded_assets(dataset, golden)

    assert report.rule_consistency_passed
    assert counts["categories"] == {"TICKET_CREATED": 1}


def test_frozen_suite_identities_cover_prompt_schema_scorer_gate_and_router() -> None:
    structured = structured_identity(code_commit="a" * 40)
    grounded = grounded_identity(code_commit="a" * 40)

    for identity in (structured, grounded):
        assert len(identity.prompt_sha256) == 64
        assert len(identity.schema_sha256) == 64
        assert len(identity.normalization_sha256) == 64
        assert len(identity.scorer_sha256) == 64
        assert len(identity.gate_sha256) == 64
        assert identity.provider_router_version == "deepseek-flash-pro-router-v1"

    root = Path(__file__).parents[5]
    assert structured.scorer_sha256 == sha256_file(
        root / "backend" / "evals" / "holdout" / "structured_scorer_v1.json"
    )
