"""Read-only, sanitized operator projection over an associated Resident thread."""

from uuid import UUID

from app.agent_runtime.models import AgentCallerContext, RunStatus
from app.agent_runtime.orchestration import AgentOrchestrator
from app.api.schemas.agent import OperatorThreadResponse
from app.application.auth import AuthenticatedIdentity
from app.domain.enums import WorkflowStage


class OperatorThreadReviewService:
    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def review(
        self, identity: AuthenticatedIdentity, *, thread_id: UUID, trace_id: UUID
    ) -> OperatorThreadResponse | None:
        state = await self._orchestrator.get_operator_state(
            thread_id,
            AgentCallerContext(
                actor_type=identity.actor_type,
                actor_id=identity.actor_id,
                user_id=identity.user_id,
            ),
            trace_id,
        )
        if state is None:
            return None
        return OperatorThreadResponse(
            thread_id=state.thread_id,
            workflow_stage=state.workflow_stage,
            run_status=state.run_status,
            task_intent=state.task_intent,
            issue_category=state.issue_category,
            issue_location=state.issue_location,
            severity=state.severity,
            policy_sufficiency=state.policy_sufficiency,
            policy_conflict=state.policy_conflict,
            policy_evidence_summary=state.policy_evidence_ids,
            missing_fields=state.missing_fields,
            active_ticket_id=state.active_ticket_id,
            active_appointment_id=state.active_appointment_id,
            human_review_required=(
                state.run_status is RunStatus.NEEDS_HUMAN_REVIEW
                or state.workflow_stage
                in {WorkflowStage.HUMAN_REVIEW, WorkflowStage.EMERGENCY_REVIEW}
            ),
            updated_at=state.updated_at,
        )
