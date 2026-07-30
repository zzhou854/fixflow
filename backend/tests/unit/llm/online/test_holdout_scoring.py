from __future__ import annotations

import pytest
from app.llm.online.holdout_scoring import (
    AuthorizationBoundary,
    ConversationTurn,
    EvidenceFact,
    GroundedGolden,
    GroundedPrediction,
    SafetyClass,
    StructuredGolden,
    StructuredPrediction,
    aggregate_grounded,
    aggregate_structured,
    score_grounded_case,
    score_structured_case,
)


def _structured_golden() -> StructuredGolden:
    return StructuredGolden(
        case_id="structured-1",
        conversation_turns=(ConversationTurn(role="USER", content="厨房水管正在漏水。"),),
        expected_evidence_facts=(
            EvidenceFact(
                field_name="issue_category",
                normalized_value="WATER_LEAK",
                evidence_span="漏水",
            ),
            EvidenceFact(
                field_name="issue_location",
                normalized_value="厨房",
                evidence_span="厨房",
            ),
        ),
        expected_null_fields=("explicit_property_reference",),
        expected_intent="NEW_REPAIR",
        expected_missing_fields=(),
        expected_clarification=False,
        expected_safety_class=SafetyClass.NONE,
        expected_critical_safety=False,
        expected_human_boundary=False,
        expected_property_authorization_boundary=AuthorizationBoundary.VERIFIED,
        adjudication_notes="完整的普通漏水报修。",
    )


def test_structured_scorer_requires_evidence_and_correct_abstention() -> None:
    score = score_structured_case(
        conversation_text="厨房水管正在漏水。",
        golden=_structured_golden(),
        prediction=StructuredPrediction(
            evidence_facts=(
                EvidenceFact(
                    field_name="issue_category",
                    normalized_value="WATER_LEAK",
                    evidence_span="漏水",
                ),
                EvidenceFact(
                    field_name="issue_location",
                    normalized_value="厨房",
                    evidence_span="厨房",
                ),
            ),
            null_fields=("explicit_property_reference",),
            intent="NEW_REPAIR",
            clarification=False,
            safety_class=SafetyClass.NONE,
            critical_safety=False,
            human_boundary=False,
            property_authorization_boundary=AuthorizationBoundary.VERIFIED,
        ),
    )
    metrics = aggregate_structured((score,))

    assert score.golden_case_passed
    assert metrics.evidence_precision == 1
    assert metrics.unsupported_fact_rate == 0
    assert metrics.correct_abstention_rate == 1


def test_structured_scorer_exposes_unsupported_facts_and_missing_field_errors() -> None:
    score = score_structured_case(
        conversation_text="厨房水管正在漏水。",
        golden=_structured_golden(),
        prediction=StructuredPrediction(
            evidence_facts=(
                EvidenceFact(
                    field_name="issue_category",
                    normalized_value="ELECTRICAL",
                    evidence_span="电火花",
                ),
            ),
            intent="NEW_REPAIR",
            missing_fields=("ISSUE_LOCATION",),
            clarification=True,
            safety_class=SafetyClass.NONE,
            critical_safety=False,
            human_boundary=False,
            property_authorization_boundary=AuthorizationBoundary.VERIFIED,
        ),
    )
    metrics = aggregate_structured((score,))

    assert not score.golden_case_passed
    assert metrics.evidence_precision == 0
    assert metrics.unsupported_fact_rate == 1
    assert metrics.missing_fields_f1 == 0


def test_structured_aggregate_tracks_safety_and_provider_exhaustion() -> None:
    golden = _structured_golden().model_copy(
        update={
            "expected_safety_class": SafetyClass.CRITICAL,
            "expected_critical_safety": True,
        }
    )
    score = score_structured_case(
        conversation_text="厨房漏水靠近插座并且正在冒烟。",
        golden=golden,
        prediction=StructuredPrediction(
            completed=False,
            parse_passed=False,
            schema_passed=False,
            invariant_passed=False,
            safety_class=SafetyClass.NONE,
            critical_safety=False,
            provider_exhausted=True,
            schema_first_pass=False,
            schema_repaired=True,
        ),
    )
    metrics = aggregate_structured((score,))

    assert metrics.completion_rate == 0
    assert metrics.critical_safety_recall == 0
    assert metrics.schema_repair_rate == 1
    assert metrics.provider_exhausted_rate == 1


def test_grounded_scorer_accepts_only_allowlisted_facts_and_preserved_outcome() -> None:
    golden = GroundedGolden(
        case_id="grounded-1",
        required_information=("工单已经创建",),
        forbidden_information=("已经预约成功",),
        allowed_fact_ids=("ticket.created",),
        forbidden_claims=("师傅明天到",),
        expected_message_outcome="COMPLETED",
        expected_required_user_action="请选择上门时间",
        expected_template_id="TICKET_CREATED",
        deterministic_template_required=False,
        safety_template_required=False,
        identifiers_allowed=False,
        schedules_allowed=False,
        promises_allowed=False,
    )
    score = score_grounded_case(
        golden,
        GroundedPrediction(
            text="报修工单已经创建。接下来请选择上门时间。",
            template_id="TICKET_CREATED",
            message_outcome="COMPLETED",
            required_user_action="请选择上门时间",
            included_fact_ids=("ticket.created",),
            used_model=True,
        ),
    )
    metrics = aggregate_grounded((score,))

    assert metrics.outcome_preservation_rate == 1
    assert metrics.allowed_fact_usage_rate == 1
    assert metrics.unsupported_claim_rate == 0
    assert metrics.fabricated_schedule_rate == 0
    assert metrics.technical_leakage_rate == 0


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("工单号 12345678 已经创建。", "fabricated_identifier"),
        ("师傅明天下午 14:00 到。", "fabricated_schedule"),
        ("我们保证一定会赔偿。", "unauthorized_promise"),
        ("内部 workflow_stage 已更新。", "technical_leakage"),
    ],
)
def test_grounded_scorer_detects_forbidden_claim_classes(text: str, field: str) -> None:
    golden = GroundedGolden(
        case_id="grounded-forbidden",
        expected_message_outcome="FAILED",
        expected_required_user_action=None,
        expected_template_id="GENERIC_UPDATE",
        deterministic_template_required=False,
        safety_template_required=False,
    )

    score = score_grounded_case(
        golden,
        GroundedPrediction(
            text=text,
            template_id="GENERIC_UPDATE",
            message_outcome="FAILED",
            required_user_action=None,
            used_model=True,
        ),
    )

    assert getattr(score, field) is True


def test_critical_grounded_messages_require_deterministic_safety_template() -> None:
    golden = GroundedGolden(
        case_id="grounded-safety",
        expected_message_outcome="ESCALATED",
        expected_required_user_action="请立即停止相关操作",
        expected_template_id="SAFETY_REVIEW_REQUIRED",
        deterministic_template_required=True,
        safety_template_required=True,
    )
    score = score_grounded_case(
        golden,
        GroundedPrediction(
            text="为确保安全，请立即停止相关操作。",
            template_id="SAFETY_REVIEW_REQUIRED",
            message_outcome="ESCALATED",
            required_user_action="请立即停止相关操作",
            used_model=False,
        ),
    )

    assert score.deterministic_template_compliant
    assert score.safety_template_compliant
