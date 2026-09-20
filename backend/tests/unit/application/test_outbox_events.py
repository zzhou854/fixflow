from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.application.events import AggregateType, DomainEvent, DomainEventType, build_domain_event
from app.application.models import MutationMetadata
from app.domain.enums import ActorType


def test_event_identity_ignores_trace_and_time_but_changes_with_aggregate_version() -> None:
    actor_id, aggregate_id = uuid4(), uuid4()
    first_metadata = MutationMetadata(
        actor_type=ActorType.RESIDENT,
        actor_id=actor_id,
        trace_id=uuid4(),
        idempotency_key="stable-request",
        occurred_at=datetime.now(UTC),
    )
    retried_metadata = MutationMetadata(
        actor_type=ActorType.RESIDENT,
        actor_id=actor_id,
        trace_id=uuid4(),
        idempotency_key="stable-request",
        occurred_at=first_metadata.occurred_at + timedelta(minutes=5),
    )

    def build(metadata: MutationMetadata, version: int) -> DomainEvent:
        return build_domain_event(
            event_type=DomainEventType.TICKET_STATUS_CHANGED,
            aggregate_type=AggregateType.TICKET,
            aggregate_id=aggregate_id,
            aggregate_version=version,
            metadata=metadata,
            scope="review_repair",
            payload={"from_status": "PENDING_ACCEPTANCE", "to_status": "CLOSED"},
        )

    first = build(first_metadata, 4)
    retry = build(retried_metadata, 4)
    next_version = build(first_metadata, 5)
    assert first.event_key == retry.event_key
    assert first.event_id == retry.event_id
    assert first.operation_id == retry.operation_id
    assert next_version.event_key != first.event_key
