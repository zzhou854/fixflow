from app.llm.online.holdout_scoring import (
    GroundedGolden,
    GroundedPrediction,
    aggregate_grounded,
    score_grounded_case,
)


def _golden() -> GroundedGolden:
    return GroundedGolden(
        case_id="natural-status-1",
        required_information=("正在安排维修",),
        forbidden_information=("保证今天到达",),
        allowed_fact_ids=("ticket.status",),
        forbidden_claims=("物业承担全部赔偿",),
        expected_message_outcome="COMPLETED",
        expected_required_user_action="NONE",
        expected_template_id="STATUS_UPDATE",
        deterministic_template_required=False,
        grounded_generation_allowed=True,
        safety_template_required=False,
    )


def test_natural_grounded_metrics_cover_completion_facts_and_semantics() -> None:
    score = score_grounded_case(
        _golden(),
        GroundedPrediction(
            text="正在安排维修",
            template_id="STATUS_UPDATE",
            message_outcome="COMPLETED",
            required_user_action="NONE",
            included_fact_ids=("ticket.status",),
            used_model=True,
        ),
    )
    metrics = aggregate_grounded((score,))

    assert metrics.natural_response_completion_rate == 1.0
    assert metrics.allowed_fact_precision == 1.0
    assert metrics.semantic_template_compliance == 1.0


def test_unapproved_fact_and_changed_outcome_fail_new_hard_metrics() -> None:
    score = score_grounded_case(
        _golden(),
        GroundedPrediction(
            text="正在安排维修",
            template_id="STATUS_UPDATE",
            message_outcome="FAILED",
            required_user_action="NONE",
            included_fact_ids=("internal.trace",),
            used_model=True,
        ),
    )
    metrics = aggregate_grounded((score,))

    assert metrics.allowed_fact_precision == 0.0
    assert metrics.semantic_template_compliance == 0.0


def test_missing_natural_response_fails_completion_metric() -> None:
    score = score_grounded_case(
        _golden(),
        GroundedPrediction(
            completed=False,
            text="请求未完成",
            template_id="STATUS_UPDATE",
            message_outcome="COMPLETED",
            required_user_action="NONE",
            used_model=False,
        ),
    )

    assert aggregate_grounded((score,)).natural_response_completion_rate == 0.0
