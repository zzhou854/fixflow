from pathlib import Path

from app.application.agent_reliability_models import MessageOutcome, RequiredUserAction


def test_public_message_outcome_and_action_values_are_frozen() -> None:
    assert {item.value for item in MessageOutcome} == {"COMPLETED", "FAILED", "ESCALATED"}
    assert {item.value for item in RequiredUserAction} == {
        "NONE",
        "PROVIDE_DETAILS",
        "SELECT_SLOT",
        "CONFIRM_ACTION",
        "CONTACT_OPERATOR",
        "RETRY",
    }


def test_frontend_contract_is_derived_from_the_same_machine_values() -> None:
    source = Path("frontend/src/messageContract.ts").read_text(encoding="utf-8")
    for value in (*MessageOutcome, *RequiredUserAction):
        assert f"'{value.value}'" in source
