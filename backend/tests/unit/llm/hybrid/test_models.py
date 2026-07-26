from __future__ import annotations

import pytest
from app.llm.hybrid.models import EvidenceSpan, ExtractedResidentFactsV1
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


def test_positive_fact_requires_matching_evidence_shape() -> None:
    with pytest.raises(ValidationError, match="human request"):
        empty_facts(explicit_human_request=True)
    facts = empty_facts(
        explicit_human_request=True,
        human_request_evidence=EvidenceSpan(text="转人工"),
    )
    assert facts.explicit_human_request is True


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


def test_fact_prompt_is_content_addressed_and_does_not_expose_final_intent() -> None:
    first = FactPromptRegistry().resident_fact_extraction()
    second = FactPromptRegistry().resident_fact_extraction()
    assert first == second
    assert first.prompt_id == "resident_fact_extraction"
    assert first.schema_version == "resident-facts-v1"
    properties = first.output_schema["properties"]
    assert isinstance(properties, dict)
    assert "utterance_intent" not in properties
    assert "model_suggested_missing_fields" not in properties
    assert "clarification_needed" not in properties
