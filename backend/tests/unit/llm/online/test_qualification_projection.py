from app.llm.online.qualification_projection import (
    GroundedQualificationRecord,
    StructuredQualificationRecord,
    project_grounded_provider_input,
    project_structured_provider_input,
    provider_input_hash,
)


def test_structured_projection_excludes_all_evaluation_metadata() -> None:
    record = StructuredQualificationRecord.model_validate(
        {
            "case_id": "private-1",
            "category": "SAFETY",
            "expected_intent": "NEW_REPAIR",
            "golden": {"answer": "secret"},
            "adjudication_notes": "private",
            "input": {"current_user_message": "厨房水管接口正在滴水。"},
        }
    )
    assert project_structured_provider_input(record) == {
        "current_user_message": "厨房水管接口正在滴水。"
    }


def test_grounded_projection_excludes_comparison_and_golden() -> None:
    record = GroundedQualificationRecord.model_validate(
        {
            "case_id": "private-grounded-1",
            "comparison_text": "must never reach provider",
            "required_information": ["private"],
            "input": {"template_id": "GENERIC_UPDATE", "facts": []},
        }
    )
    assert project_grounded_provider_input(record) == {
        "template_id": "GENERIC_UPDATE",
        "facts": [],
    }


def test_evaluation_metadata_does_not_change_provider_request_hash() -> None:
    first = StructuredQualificationRecord.model_validate(
        {"category": "A", "input": {"current_user_message": "门锁无法上锁。"}}
    )
    second = StructuredQualificationRecord.model_validate(
        {
            "category": "B",
            "golden": {"changed": True},
            "input": {"current_user_message": "门锁无法上锁。"},
        }
    )
    assert provider_input_hash(project_structured_provider_input(first)) == provider_input_hash(
        project_structured_provider_input(second)
    )
