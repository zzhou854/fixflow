"""Projection between canonical Agent State and replay-safe state."""

from __future__ import annotations

import hashlib
from uuid import NAMESPACE_URL, uuid5

from app.agent.state import AgentConversationMessage, AgentState, PendingOperation
from app.replay.canonical import sha256_fingerprint
from app.replay.models import (
    PendingOperationProjection,
    ReconciliationProjection,
    ReplayMessageMetadata,
    ReplaySafeAgentState,
    ThreadOwnerProjection,
)


def _message_metadata(message: AgentConversationMessage) -> ReplayMessageMetadata:
    content = message.content
    return ReplayMessageMetadata(
        message_id=message.message_id,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        content_length=len(content),
        language="zh-CN",
        message_role=message.role,
    )


def project_agent_state(
    state: AgentState, *, run_status: str | None = None
) -> ReplaySafeAgentState:
    owner = None
    if state.property_context_verified and state.property_id is not None:
        owner = ThreadOwnerProjection(
            actor_type=state.actor_type,
            actor_id=state.actor_id,
            user_id=state.user_id,
            property_id=state.property_id,
        )
    pending = None
    if state.pending_operation is not None:
        operation_id = uuid5(
            NAMESPACE_URL,
            f"fixflow:operation:{state.actor_type.value}:{state.actor_id}:"
            f"{state.pending_operation.idempotency_key}",
        )
        target_id = state.active_appointment_id or state.active_ticket_id
        target_type = "APPOINTMENT" if state.active_appointment_id else "TICKET"
        expected_version = (
            state.pending_operation.expected_appointment_version
            or state.pending_operation.expected_ticket_version
        )
        pending = PendingOperationProjection(
            operation_id=operation_id,
            action=state.pending_operation.action,
            request_fingerprint=state.pending_operation.request_fingerprint,
            target_entity_type=target_type if target_id else None,
            target_entity_id=target_id,
            expected_version=expected_version,
            normalized_payload_fingerprint=state.pending_operation.request_fingerprint,
        )
    reconciliation = None
    if (
        state.pending_reconciliation_case_id is not None
        or state.pending_reconciliation_status is not None
    ):
        reconciliation = ReconciliationProjection(
            case_id=state.pending_reconciliation_case_id,
            status=state.pending_reconciliation_status,
            action=state.pending_reconciliation_action,
        )
    return ReplaySafeAgentState(
        thread_id=state.thread_id,
        trace_id=state.trace_id,
        actor_type=state.actor_type,
        actor_id=state.actor_id,
        user_id=state.user_id,
        thread_owner_identity=owner,
        property_id=state.property_id,
        property_context_verified=state.property_context_verified,
        workflow_stage=state.workflow_stage,
        run_status=run_status,
        task_intent=state.task_intent,
        utterance_intent=state.utterance_intent,
        intent_version=state.intent_version,
        issue_category=state.issue_category,
        issue_location=state.issue_location,
        normalized_issue_location=state.normalized_issue_location,
        safe_issue_summary=state.issue_description,
        severity=state.severity,
        safety_flags=state.safety_flags,
        safety_review_required=state.safety_review_required,
        missing_fields=state.missing_fields,
        policy_sufficiency=state.policy_sufficiency,
        policy_conflict=state.policy_conflict,
        policy_evidence_ids=state.policy_evidence_ids,
        missing_policy_topics=state.missing_policy_topics,
        active_ticket_id=state.active_ticket_id,
        active_appointment_id=state.active_appointment_id,
        active_ticket_snapshot=state.cached_ticket_snapshot,
        ticket_snapshot_version=state.ticket_snapshot_version,
        appointment_version=state.appointment_version,
        duplicate_candidate_summaries=state.duplicate_ticket_candidates,
        duplicate_candidates_fingerprint=state.duplicate_candidates_fingerprint,
        slot_candidate_summaries=state.candidate_slots,
        slot_candidates_fingerprint=state.candidate_slots_fingerprint,
        selected_candidate_slot=state.selected_candidate_slot,
        user_availability_windows=state.user_availability_windows,
        pending_action=state.pending_action,
        last_tool_result=state.last_tool_result,
        pending_operation_projection=pending,
        reconciliation_projection=reconciliation,
        reference_time=state.current_reference_time,
        timezone_name=state.current_timezone_name,
        service_duration_minutes=state.service_duration_minutes,
        conversation_metadata=tuple(
            _message_metadata(item) for item in state.conversation_messages
        ),
    )


def restore_agent_state(safe: ReplaySafeAgentState) -> AgentState:
    """Restore only enough state for the formal graph; no original message text returns."""

    if safe.thread_id is None:
        raise ValueError("operator replay state cannot be restored as Agent State")
    messages = tuple(
        AgentConversationMessage(
            message_id=item.message_id,
            role=item.message_role,
            content="[REDACTED FOR REPLAY]",
            created_at=safe.reference_time,
            turn_id=safe.trace_id,
        )
        for item in safe.conversation_metadata
        if safe.reference_time is not None
    )
    pending = None
    if safe.pending_operation_projection is not None:
        item = safe.pending_operation_projection
        pending = PendingOperation(
            action=item.action,
            request_fingerprint=item.request_fingerprint,
            idempotency_key=f"replay-redacted-{item.request_fingerprint[:32]}",
            intent_version=safe.intent_version,
            expected_ticket_version=(
                item.expected_version if item.target_entity_type == "TICKET" else None
            ),
            expected_appointment_version=(
                item.expected_version if item.target_entity_type == "APPOINTMENT" else None
            ),
        )
    reconciliation = safe.reconciliation_projection
    current_message = (
        "[REDACTED FOR REPLAY]"
        if safe.reference_time is not None and safe.timezone_name is not None
        else None
    )
    return AgentState(
        thread_id=safe.thread_id,
        trace_id=safe.trace_id,
        actor_type=safe.actor_type,
        actor_id=safe.actor_id,
        user_id=safe.user_id,
        property_id=safe.property_id,
        property_context_verified=safe.property_context_verified,
        active_ticket_id=safe.active_ticket_id,
        active_appointment_id=safe.active_appointment_id,
        cached_ticket_snapshot=safe.active_ticket_snapshot,
        task_intent=safe.task_intent,
        utterance_intent=safe.utterance_intent,
        intent_version=safe.intent_version,
        issue_category=safe.issue_category,
        issue_location=safe.issue_location,
        normalized_issue_location=safe.normalized_issue_location,
        issue_description=safe.safe_issue_summary,
        severity=safe.severity,
        service_duration_minutes=safe.service_duration_minutes,
        safety_flags=safe.safety_flags,
        user_availability_windows=safe.user_availability_windows,
        candidate_slots=safe.slot_candidate_summaries,
        candidate_slots_fingerprint=safe.slot_candidates_fingerprint,
        selected_candidate_slot=safe.selected_candidate_slot,
        duplicate_ticket_candidates=safe.duplicate_candidate_summaries,
        duplicate_candidates_fingerprint=safe.duplicate_candidates_fingerprint,
        missing_fields=safe.missing_fields,
        policy_evidence_ids=safe.policy_evidence_ids,
        policy_conflict=safe.policy_conflict,
        policy_sufficiency=safe.policy_sufficiency,
        missing_policy_topics=safe.missing_policy_topics,
        safety_review_required=safe.safety_review_required,
        workflow_stage=safe.workflow_stage,
        pending_action=safe.pending_action,
        ticket_snapshot_version=safe.ticket_snapshot_version,
        appointment_version=safe.appointment_version,
        last_tool_result=safe.last_tool_result,
        pending_operation=pending,
        pending_reconciliation_case_id=reconciliation.case_id if reconciliation else None,
        pending_reconciliation_status=reconciliation.status if reconciliation else None,
        pending_reconciliation_action=reconciliation.action if reconciliation else None,
        conversation_messages=messages,
        current_user_message=current_message,
        current_reference_time=safe.reference_time,
        current_timezone_name=safe.timezone_name,
    )


def state_fingerprint(state: ReplaySafeAgentState) -> str:
    payload = state.model_dump(
        mode="json",
        exclude={
            "trace_id",
            "run_status",
            "conversation_metadata",
        },
    )
    snapshot = payload.get("active_ticket_snapshot")
    if isinstance(snapshot, dict):
        # Snapshot observation time is local telemetry. The versioned domain
        # facts and appointment contents are replay control inputs.
        snapshot.pop("observed_at", None)
    return sha256_fingerprint(payload)
