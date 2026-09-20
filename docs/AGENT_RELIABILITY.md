# Agent message reliability and human review

## Formal result boundary

Every resident message or typed Resume produces one public outcome:
`COMPLETED`, `FAILED`, or `ESCALATED`. `COMPLETED` means the current message
was handled; it also covers a successful request for more details, slot
selection, or confirmation. `ESCALATED` is valid only after an open
`human_review_cases` row commits. `FAILED` means neither automatic handling nor
durable handoff was completed.

The corresponding required action is one of `NONE`, `PROVIDE_DETAILS`,
`SELECT_SLOT`, `CONFIRM_ACTION`, `CONTACT_OPERATOR`, or `RETRY`.

The transaction order is:

```text
durable user/assistant messages
-> Agent Run terminal status and public outcome
-> optional human-review case/history/outbox
-> commit
-> HTTP response
-> bounded SSE projection
```

`agent_messages` and `agent_runs` are the formal result facts. The LangGraph
Checkpoint remains a recovery snapshot. HTTP returns the committed result; SSE
is a non-persistent delivery aid.

## Terminal events and stalled runs

The existing run lifecycle terminal (`run_completed`, `run_interrupted`,
`run_failed_safe`, or `run_failed`) remains available for technical audit. A
separate public terminal is exactly one of `message.completed`,
`message.failed`, or `message.escalated`. PostgreSQL partial unique indexes
allow at most one of each terminal family per Run.

The lifespan-owned stalled-run monitor periodically locks old `RUNNING` rows.
It derives the public result only from durable messages and human-review facts;
it never replays a mutation. Reconciliation is idempotent and creates a safe
failure result when completion cannot be proved.

## Human-review cases

Pre-ticket failures do not create fake repair tickets. A case may therefore
have no `property_id` or `ticket_id`. The typed failure stage, reason, safety
level, sanitized summary, owner, assignment, timestamps, and version are
persisted. The active-case key is:

```text
SHA256(thread_id + intent_version + failure_stage)
```

A partial unique index over `OPEN` and `CLAIMED` prevents concurrent duplicate
work. Case creation, state history, and Outbox evidence share the message
finalization transaction. Allowed transitions are:

```text
OPEN -> CLAIMED | RESOLVED | DISMISSED
CLAIMED -> OPEN | RESOLVED | DISMISSED
```

Only an authenticated Operator can list or transition these cases. Closed
cases are terminal.

## Thread registry and archive

`agent_thread_records` is the resident-owned conversation registry. The default
list returns the five most recently active, non-archived threads. Archive and
restore use optimistic versions and never delete messages, Checkpoints, Trace,
Replay, tickets, appointments, or human-review records. Historical rows are
backfilled only when resident ownership and property identity are unambiguous.

The default provider remains `scripted`. This reliability phase does not
activate DeepSeek or any other online provider.
