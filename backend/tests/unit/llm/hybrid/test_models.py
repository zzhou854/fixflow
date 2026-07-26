from __future__ import annotations

import pytest
from app.llm.hybrid.models import (
    EvidenceSpan,
    ExtractedAvailabilityWindow,
    ExtractedResidentFactsV1,
)
from app.llm.hybrid.normalizer import ResidentFactNormalizer
from app.llm.hybrid.prompts import FactPromptRegistry
from pydantic import ValidationError

from tests.unit.llm.hybrid.conftest import empty_facts


def test_fact_schema_is_closed_and_serialization_is_deterministic() -> None:
    facts = empty_facts()
    assert facts.model_dump_json() == empty_facts().model_dump_json()
    with pytest.raises(ValidationError, match="extra"):
        ExtractedResidentFactsV1.model_validate(
            {**facts.model_dump(), "utterance_intent": "NEW_REPAIR"}
        )


def test_provider_cross_field_conflict_is_normalized_not_parse_retried() -> None:
    extracted = empty_facts(explicit_human_request=True)
    normalized = ResidentFactNormalizer().normalize(
        extracted,
        current_user_message="厨房漏水，请安排维修。",
    )
    assert normalized.explicit_human_request is False
    assert normalized.rejected_evidence_count == 1

    facts = empty_facts(
        explicit_human_request=True,
        human_request_evidence=EvidenceSpan(text="转人工"),
    )
    assert facts.explicit_human_request is True


def test_invalid_provider_time_window_is_rejected_not_parse_retried() -> None:
    extracted = empty_facts(
        availability_windows=(
            ExtractedAvailabilityWindow(
                starts_at="2026-07-27T15:00:00+08:00",
                ends_at="2026-07-27T14:00:00+08:00",
            ),
        )
    )
    normalized = ResidentFactNormalizer().normalize(
        extracted,
        current_user_message="最后那个时间可以。",
    )
    assert normalized.availability_windows == ()
    assert normalized.rejected_evidence_count == 1


def test_unverifiable_evidence_is_rejected_before_decision() -> None:
    extracted = empty_facts(
        explicit_human_request=True,
        human_request_evidence=EvidenceSpan(text="不存在的人工请求"),
    )
    normalized = ResidentFactNormalizer().normalize(
        extracted,
        current_user_message="厨房漏水，请安排维修。",
    )
    assert normalized.explicit_human_request is False
    assert normalized.human_request_evidence is None
    assert normalized.rejected_evidence_count == 1


def test_unrelated_but_present_evidence_cannot_establish_business_fact() -> None:
    extracted = empty_facts(
        explicit_human_request=True,
        human_request_evidence=EvidenceSpan(text="请安排维修"),
        booking_request_mentioned=True,
        booking_request_evidence=EvidenceSpan(text="请安排维修"),
    )
    normalized = ResidentFactNormalizer().normalize(
        extracted,
        current_user_message="厨房漏水，请安排维修。",
    )
    assert normalized.explicit_human_request is False
    assert normalized.booking_request_mentioned is False
    assert normalized.rejected_evidence_count == 2


@pytest.mark.parametrize("message", ("坏了。", "麻烦帮我处理一下。", "[图片]"))
def test_vague_evidence_cannot_satisfy_issue_description(message: str) -> None:
    extracted = empty_facts(
        issue_description_present=True,
        issue_description_text=message,
        issue_description_evidence=EvidenceSpan(text=message),
    )
    normalized = ResidentFactNormalizer().normalize(
        extracted,
        current_user_message=message,
    )
    assert normalized.issue_description_present is False
    assert normalized.issue_description_text is None


def test_non_spatial_evidence_cannot_satisfy_location() -> None:
    extracted = empty_facts(
        location_mentioned=True,
        location_text="帮我处理一下",
        location_evidence=EvidenceSpan(text="帮我处理一下"),
    )
    normalized = ResidentFactNormalizer().normalize(
        extracted,
        current_user_message="麻烦帮我处理一下。",
    )
    assert normalized.location_mentioned is False
    assert normalized.location_text is None


def test_model_cannot_label_vague_repair_as_small_talk() -> None:
    normalized = ResidentFactNormalizer().normalize(
        empty_facts(small_talk_only=True),
        current_user_message="坏了。",
    )
    assert normalized.small_talk_only is False
    assert normalized.rejected_evidence_count == 1


def test_fact_prompt_is_content_addressed_and_does_not_expose_final_intent() -> None:
    first = FactPromptRegistry().resident_fact_extraction()
    second = FactPromptRegistry().resident_fact_extraction()
    assert first == second
    assert first.prompt_id == "resident_fact_extraction"
    assert first.schema_version == "resident-facts-v2"
    properties = first.output_schema["properties"]
    assert isinstance(properties, dict)
    assert "utterance_intent" not in properties
    assert "model_suggested_missing_fields" not in properties
    assert "clarification_needed" not in properties
