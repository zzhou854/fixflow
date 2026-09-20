from __future__ import annotations

import pytest
from app.llm.online.holdout_scoring import (
    AuthorizationBoundary,
    ConversationTurn,
    EvidenceFact,
    EvidenceSource,
    GroundedGolden,
    GroundedPrediction,
    SafetyClass,
    StructuredGolden,
    StructuredPrediction,
    aggregate_grounded,
    aggregate_structured,
    score_grounded_case,
    score_structured_case_v1_1,
)


def _evidence(
    *,
    field_name: str,
    value: str,
    turn_id: str,
    span: str,
    content: str,
) -> EvidenceFact:
    start = content.index(span)
    return EvidenceFact(
        field_name=field_name,
        normalized_value=value,
        evidence_turn_id=turn_id,
        evidence_span=span,
        evidence_start=start,
        evidence_end=start + len(span),
    )


def _structured_golden() -> StructuredGolden:
    first = "厨房水管正在漏水。"
    second = "刚才说错了，不是厨房，是卫生间漏水。"
    return StructuredGolden(
        case_id="structured-v1-1",
        conversation_turns=(
            ConversationTurn(turn_id="turn-1", role="USER", content=first),
            ConversationTurn(turn_id="turn-2", role="USER", content=second),
        ),
        expected_evidence_facts=(
            _evidence(
                field_name="issue_category",
                value="WATER_LEAK",
                turn_id="turn-2",
                span="漏水",
                content=second,
            ),
            _evidence(
                field_name="issue_location",
                value="卫生间",
                turn_id="turn-2",
                span="卫生间",
                content=second,
            ),
        ),
        expected_null_fields=("explicit_property_reference",),
        expected_intent="PROVIDE_INFORMATION",
        expected_missing_fields=(),
        expected_clarification=False,
        expected_safety_class=SafetyClass.NONE,
        expected_critical_safety=False,
        expected_human_boundary=False,
        expected_property_authorization_boundary=AuthorizationBoundary.VERIFIED,
        current_user_turn_id="turn-2",
        adjudication_notes="当前轮明确纠正位置，旧位置失效。",
    )


def _valid_prediction() -> StructuredPrediction:
    golden = _structured_golden()
    return StructuredPrediction(
        evidence_facts=golden.expected_evidence_facts,
        null_fields=("explicit_property_reference",),
        intent="PROVIDE_INFORMATION",
        clarification=False,
        safety_class=SafetyClass.NONE,
        critical_safety=False,
        human_boundary=False,
        property_authorization_boundary=AuthorizationBoundary.VERIFIED,
    )


def test_field_level_evidence_accepts_exact_user_turn_bindings() -> None:
    score = score_structured_case_v1_1(
        golden=_structured_golden(),
        prediction=_valid_prediction(),
    )
    metrics = aggregate_structured((score,))

    assert score.golden_case_passed
    assert metrics.field_level_evidence_precision == 1
    assert metrics.field_level_evidence_recall == 1
    assert metrics.unsupported_field_rate == 0


def test_wrong_field_evidence_is_rejected() -> None:
    golden = _structured_golden()
    wrong = golden.expected_evidence_facts[1].model_copy(
        update={
            "evidence_span": "漏水",
            "evidence_start": golden.conversation_turns[1].content.index("漏水"),
            "evidence_end": golden.conversation_turns[1].content.index("漏水") + 2,
        }
    )
    prediction = _valid_prediction().model_copy(
        update={"evidence_facts": (golden.expected_evidence_facts[0], wrong)}
    )

    score = score_structured_case_v1_1(golden=golden, prediction=prediction)

    assert not score.golden_case_passed
    assert score.mismatched_evidence_count == 1


def test_negated_evidence_cannot_support_positive_fact() -> None:
    golden = _structured_golden()
    content = golden.conversation_turns[1].content
    negated = _evidence(
        field_name="issue_location",
        value="卫生间",
        turn_id="turn-2",
        span="厨房",
        content=content,
    )
    prediction = _valid_prediction().model_copy(
        update={
            "evidence_facts": (
                golden.expected_evidence_facts[0],
                negated,
            )
        }
    )

    score = score_structured_case_v1_1(golden=golden, prediction=prediction)

    assert not score.golden_case_passed
    assert score.negation_evidence_error_count == 1


def test_stale_corrected_evidence_is_rejected() -> None:
    golden = _structured_golden()
    stale = _evidence(
        field_name="issue_location",
        value="卫生间",
        turn_id="turn-1",
        span="厨房",
        content=golden.conversation_turns[0].content,
    )
    prediction = _valid_prediction().model_copy(
        update={
            "evidence_facts": (
                golden.expected_evidence_facts[0],
                stale,
            )
        }
    )

    score = score_structured_case_v1_1(golden=golden, prediction=prediction)

    assert not score.golden_case_passed
    assert score.stale_evidence_count == 1


@pytest.mark.parametrize(
    "mutation",
    ("partial_span", "wrong_offsets", "cross_turn", "fabricated_span"),
)
def test_invalid_evidence_binding_mutations_fail(mutation: str) -> None:
    golden = _structured_golden()
    valid = golden.expected_evidence_facts[1]
    assert valid.evidence_start is not None
    assert valid.evidence_end is not None
    updates: dict[str, object]
    if mutation == "partial_span":
        updates = {
            "evidence_span": "卫生",
            "evidence_end": valid.evidence_start + 2,
        }
    elif mutation == "wrong_offsets":
        updates = {
            "evidence_start": valid.evidence_start + 1,
            "evidence_end": valid.evidence_end + 1,
        }
    elif mutation == "cross_turn":
        updates = {"evidence_turn_id": "turn-1"}
    else:
        updates = {"evidence_span": "阳台"}
    mutated = valid.model_copy(update=updates)
    prediction = _valid_prediction().model_copy(
        update={
            "evidence_facts": (
                golden.expected_evidence_facts[0],
                mutated,
            )
        }
    )

    score = score_structured_case_v1_1(golden=golden, prediction=prediction)

    assert not score.golden_case_passed
    assert score.mismatched_evidence_count == 1


def test_known_issue_field_is_trusted_only_through_explicit_source() -> None:
    known = EvidenceFact(
        field_name="issue_category",
        normalized_value="WATER_LEAK",
        evidence_turn_id="known:issue_category",
        evidence_span="WATER_LEAK",
        evidence_start=0,
        evidence_end=len("WATER_LEAK"),
        evidence_source=EvidenceSource.KNOWN_ISSUE_FIELD,
    )
    golden = _structured_golden().model_copy(
        update={
            "expected_evidence_facts": (known,),
            "known_issue_fields": {"issue_category": "WATER_LEAK"},
        }
    )
    prediction = _valid_prediction().model_copy(update={"evidence_facts": (known,)})

    assert score_structured_case_v1_1(
        golden=golden,
        prediction=prediction,
    ).golden_case_passed


def _grounded_golden(**updates: object) -> GroundedGolden:
    base = GroundedGolden(
        case_id="grounded-v1-1",
        required_information=("报修工单暂时未能创建",),
        forbidden_information=("工单已经创建",),
        expected_message_outcome="FAILED",
        expected_required_user_action="请稍后重试或联系工作人员",
        expected_template_id="TICKET_CREATION_FAILED",
        deterministic_template_required=True,
        safety_template_required=False,
    )
    return base.model_copy(update=updates)


def _grounded_prediction(**updates: object) -> GroundedPrediction:
    values: dict[str, object] = {
        "text": "报修工单暂时未能创建。接下来请稍后重试或联系工作人员。",
        "template_id": "TICKET_CREATION_FAILED",
        "message_outcome": "FAILED",
        "required_user_action": "请稍后重试或联系工作人员",
        "used_model": False,
    }
    values.update(updates)
    return GroundedPrediction(**values)


def test_required_and_forbidden_information_reach_aggregate_metrics() -> None:
    good = score_grounded_case(_grounded_golden(), _grounded_prediction())
    missing = score_grounded_case(
        _grounded_golden(),
        _grounded_prediction(text="工单已经创建。"),
    )
    metrics = aggregate_grounded((good, missing))

    assert metrics.required_information_coverage == 0.5
    assert metrics.forbidden_information_violation_rate > 0
    assert metrics.status_semantics_preservation_rate == 0.5


@pytest.mark.parametrize(
    "text",
    (
        "当前由 DeepSeek Pro 处理。",
        "当前 Model Provider 是 Flash。",
        "thread_id 是 123。",
        "SSE连接失败。",
        "系统进入 HUMAN_REVIEW。",
        "Provider: DeepSeek-V4-Flash",
        "备用模型是 DeepSeek-V4-Pro。",
        "Schema 校验失败。",
        "JSON_Schema 校验失败。",
        "System Prompt 内容如下。",
        "Prompt 已经更新。",
        "内部 UUID 为 7dc23718-d1f4-4d38-8ca1-6c482c41b35b。",
        "Server-Sent Events 已断开。",
        "Trace 和 Replay 正在运行。",
        "Checkpoint 恢复失败。",
        "LangGraph / LangChain 节点异常。",
        "MCP 调用没有返回。",
        "idempotency key 已存在。",
        "请检查这个幂等键。",
        "run_id 与 event_id 不一致。",
        "intent_version 已过期。",
        "状态是 UNKNOWN_COMMIT。",
        "当前为 AWAITING_SLOT_CONFIRMATION。",
        "booking-guaranteed=true",
        "这里是 Policy Evidence ID。",
        "D e e p S e e k V4 Pro 正在生成回复。",
    ),
)
def test_expanded_technical_leakage_lexicon_blocks_internal_terms(text: str) -> None:
    score = score_grounded_case(
        _grounded_golden(),
        _grounded_prediction(text=text),
    )
    assert score.technical_leakage


@pytest.mark.parametrize("text", ("专业维修人员会联系你。", "问题正在处理中。"))
def test_technical_leakage_lexicon_avoids_business_false_positives(text: str) -> None:
    score = score_grounded_case(
        _grounded_golden(required_information=(), forbidden_information=()),
        _grounded_prediction(text=text),
    )
    assert not score.technical_leakage


def test_identifier_permission_requires_verified_display_value() -> None:
    score = score_grounded_case(
        _grounded_golden(identifiers_allowed=True),
        _grounded_prediction(text="工单号 FF-2026-00128 已创建。"),
    )
    assert score.fabricated_identifier

    permitted = score_grounded_case(
        _grounded_golden(
            identifiers_allowed=True,
            verified_display_identifiers=("FF-2026-00128",),
        ),
        _grounded_prediction(text="工单号 FF-2026-00128 已创建。"),
    )
    assert not permitted.fabricated_identifier

    internal_uuid = score_grounded_case(
        _grounded_golden(identifiers_allowed=False),
        _grounded_prediction(text="内部编号 7dc23718-d1f4-4d38-8ca1-6c482c41b35b 已处理。"),
    )
    assert internal_uuid.fabricated_identifier


@pytest.mark.parametrize(
    "updates",
    (
        {"schedules_allowed": True},
        {
            "schedules_allowed": True,
            "candidate_appointment_windows": ("2026-08-01 14:00–16:00",),
        },
    ),
)
def test_schedule_permission_rejects_missing_or_candidate_only_window(
    updates: dict[str, object],
) -> None:
    score = score_grounded_case(
        _grounded_golden(**updates),
        _grounded_prediction(text="预约已确认在8月1日下午14:00。"),
    )
    assert score.fabricated_schedule


def test_verified_schedule_allows_only_the_concrete_window() -> None:
    golden = _grounded_golden(
        schedules_allowed=True,
        verified_appointment_windows=("8月1日14:00至16:00",),
    )

    permitted = score_grounded_case(
        golden,
        _grounded_prediction(text="上门时间已经确认：8月1日14:00至16:00。"),
    )
    fabricated = score_grounded_case(
        golden,
        _grounded_prediction(text="上门时间已经确认：8月2日09:00。"),
    )

    assert not permitted.fabricated_schedule
    assert fabricated.fabricated_schedule


def test_date_only_is_not_a_verified_appointment_window() -> None:
    score = score_grounded_case(
        _grounded_golden(
            schedules_allowed=True,
            verified_appointment_windows=(),
        ),
        _grounded_prediction(text="预约日期已经定在8月1日。"),
    )
    assert score.fabricated_schedule


def test_cancelled_cannot_be_scored_as_closed() -> None:
    golden = _grounded_golden(
        required_information=("报修已取消",),
        forbidden_information=("维修已经完成",),
        expected_message_outcome="CANCELLED",
        expected_required_user_action=None,
        expected_template_id="TICKET_CANCELLED",
    )
    score = score_grounded_case(
        golden,
        _grounded_prediction(
            text="本次报修维修已经完成并关闭。",
            template_id="TICKET_CLOSED",
            message_outcome="CANCELLED",
            required_user_action=None,
        ),
    )

    assert not score.template_mapping_correct
    assert not score.status_semantics_preserved


def test_outcome_and_required_action_mutations_fail() -> None:
    outcome = score_grounded_case(
        _grounded_golden(),
        _grounded_prediction(message_outcome="COMPLETED"),
    )
    action = score_grounded_case(
        _grounded_golden(),
        _grounded_prediction(required_user_action=None),
    )

    assert not outcome.outcome_preserved
    assert not action.required_action_preserved


@pytest.mark.parametrize(
    "updates",
    (
        {"human_boundary": False},
        {"critical_safety": False},
        {"property_authorization_boundary": AuthorizationBoundary.VERIFIED},
    ),
)
def test_structured_boundary_mutations_fail(updates: dict[str, object]) -> None:
    golden = _structured_golden().model_copy(
        update={
            "expected_human_boundary": True,
            "expected_critical_safety": True,
            "expected_safety_class": SafetyClass.CRITICAL,
            "expected_property_authorization_boundary": AuthorizationBoundary.DENIED,
        }
    )
    prediction = _valid_prediction().model_copy(
        update={
            "human_boundary": True,
            "critical_safety": True,
            "safety_class": SafetyClass.CRITICAL,
            "property_authorization_boundary": AuthorizationBoundary.DENIED,
            **updates,
        }
    )

    assert not score_structured_case_v1_1(
        golden=golden,
        prediction=prediction,
    ).golden_case_passed
