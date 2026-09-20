"""Deterministic bounded projection for language interpretation input."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass

from app.agent.models import InterpretMessageInput


@dataclass(frozen=True, slots=True)
class InterpretationInputLimits:
    max_input_characters: int = 6000
    max_context_messages: int = 6
    max_message_characters: int = 2000

    def __post_init__(self) -> None:
        if (
            min(
                self.max_input_characters,
                self.max_context_messages,
                self.max_message_characters,
            )
            <= 0
        ):
            raise ValueError("interpretation input limits must be positive")


@dataclass(frozen=True, slots=True)
class SanitizedInterpretationInput:
    payload_json: str
    input_hash: str
    input_character_count: int
    context_message_count: int


def _sanitize_text(value: str, limit: int) -> str:
    cleaned = "".join(
        character
        for character in unicodedata.normalize("NFC", value.replace("\x00", ""))
        if character in "\n\t" or unicodedata.category(character) != "Cc"
    )
    return cleaned[:limit]


def build_sanitized_interpretation_input(
    request: InterpretMessageInput,
    limits: InterpretationInputLimits,
) -> SanitizedInterpretationInput:
    """Serialize only the allowlisted request projection with stable truncation."""

    current = _sanitize_text(
        request.current_user_message,
        min(limits.max_message_characters, limits.max_input_characters),
    )
    recent = request.recent_conversation_messages[-limits.max_context_messages :]
    remaining = max(0, limits.max_input_characters - len(current))
    projected: list[dict[str, str]] = []
    # Keep the newest context within the total budget, then restore message order.
    for message in reversed(recent):
        if remaining <= 0:
            break
        content = _sanitize_text(
            message.content,
            min(limits.max_message_characters, remaining),
        )
        remaining -= len(content)
        projected.append({"role": message.role.value.lower(), "content": content})
    projected.reverse()
    payload = {
        "current_message": current,
        "conversation_projection": projected,
        "workflow_stage": request.current_workflow_stage.value,
        "current_task_intent": request.current_state_summary.task_intent.value,
        "current_utterance_intent": request.current_state_summary.utterance_intent.value,
        "current_intent_version": request.current_state_summary.intent_version,
        "missing_fields": [value.value for value in request.missing_fields],
        "known_issue_fields": request.known_issue_fields.model_dump(mode="json", exclude_none=True),
        "reference_time": request.reference_time.isoformat(),
        "timezone_name": request.timezone_name,
        "locale": "zh-CN",
        "schema_version": "interpretation-result-v1",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return SanitizedInterpretationInput(
        payload_json=encoded,
        input_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        input_character_count=len(current) + sum(len(item["content"]) for item in projected),
        context_message_count=len(projected),
    )
