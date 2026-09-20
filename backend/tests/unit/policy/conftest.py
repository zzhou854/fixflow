"""Shared strict policy fixtures."""

from datetime import UTC, datetime

import pytest
from app.domain.enums import IssueCategory
from app.policy.enums import PolicyTopic
from app.policy.models import PolicyChunkInput, PolicyDocumentInput


@pytest.fixture
def policy_document() -> PolicyDocumentInput:
    return PolicyDocumentInput(
        policy_code="WATER_SAFETY",
        title="漏水安全处理演示政策",
        issue_category=IssueCategory.WATER_LEAK,
        policy_topic=PolicyTopic.SAFETY_ESCALATION,
        version=1,
        authority_rank=80,
        effective_from=datetime(2030, 1, 1, tzinfo=UTC),
        effective_to=datetime(2040, 1, 1, tzinfo=UTC),
        source_name="FixFlow synthetic policy corpus",
        source_reference="demo://water-safety/v1",
        chunks=(
            PolicyChunkInput(
                content="大量持续漏水或涉及配电区域时必须转人工安全审查。",
                search_terms=("大量漏水", "配电区域", "安全审查"),
                decision_key="safety_route",
                decision_value="manual_review",
            ),
        ),
    )
