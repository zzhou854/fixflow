# Public message outcome contract

FixFlow exposes one stable machine contract for every resident Agent run. The
language model cannot modify these values.

## Terminal outcome

`message_outcome` is exactly one of:

```text
COMPLETED
FAILED
ESCALATED
```

Business details such as waiting for information, an appointment choice,
cancellation, closure, or human review are not additional terminal outcomes.
They are represented by `business_status` and `template_id`.

## Required action

`required_user_action` is exactly one of:

```text
NONE
PROVIDE_DETAILS
SELECT_SLOT
CONFIRM_ACTION
CONTACT_OPERATOR
RETRY
```

It is a machine field and never contains Chinese display copy. Resident-facing
instructions use `display_action_text`, for example:

```json
{
  "message_outcome": "COMPLETED",
  "business_status": "APPOINTMENT_PENDING",
  "template_id": "APPOINTMENT_PENDING",
  "required_user_action": "SELECT_SLOT",
  "display_action_text": "请选择可用的上门时间"
}
```

The API, SSE terminal event, frontend TypeScript contract, online grounded
request, and revised Holdout Golden must use the same values. Older sealed
Holdout assets remain immutable even where their contract is now superseded.
