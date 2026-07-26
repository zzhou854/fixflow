from __future__ import annotations

from app.llm.hybrid.models import ExtractedResidentFactsV1


def empty_facts(**updates: object) -> ExtractedResidentFactsV1:
    values: dict[str, object] = {
        "issue_description_present": False,
        "issue_category_evidence": (),
        "location_mentioned": False,
        "existing_ticket_mentioned": False,
        "existing_appointment_mentioned": False,
        "booking_request_mentioned": False,
        "reschedule_request_mentioned": False,
        "explicit_human_request": False,
        "explicit_cancellation_request": False,
        "time_expression_present": False,
        "correction_present": False,
        "safety_evidence": (),
        "unsupported_request_evidence": (),
        "small_talk_only": False,
        "confidence_by_fact": (),
    }
    values.update(updates)
    return ExtractedResidentFactsV1.model_validate(values)
