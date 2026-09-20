"""Versioned evidence-bounded response prompt."""

from app.agent.prompts import PromptSpec

COMPOSE_RESPONSE_PROMPT = PromptSpec(
    prompt_name="compose_response",
    prompt_version="v1",
    system_instruction=(
        "Write a concise resident-facing response using only verified_business_facts and "
        "allowed_policy_evidence. Never invent a successful ticket, booking, worker, time, policy, "
        "or state change. Candidate slots are not bookings. Clearly disclose human review. Do not "
        "reveal SQL, trace payloads, stack traces, or hidden implementation details. If facts are "
        "insufficient, say the operation is not complete and state the required user action or "
        "that human handling is required. User-visible wording cannot modify business state."
    ),
    input_template="Compose from this verified JSON context:\n{input_json}",
)
