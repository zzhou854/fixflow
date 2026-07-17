# Candidate state machines

> Approval status: design candidate only. None of these enums is implemented in
> code until the user explicitly approves this document.

FixFlow separates orchestration progress from business truth:

- `workflow_stage` belongs to the future orchestrator and checkpoint;
- `ticket_status` belongs to the repair-ticket aggregate in PostgreSQL;
- `appointment_status` belongs to an appointment in PostgreSQL;
- `worker_event_type` is append-only evidence and never substitutes for either aggregate.

On every workflow resume, the latest ticket/appointment state and versions are
reread from PostgreSQL before the next action is chosen.

## Workflow stages

The minimum orchestration stages remain:

```text
INTAKE, NEED_PROPERTY, NEED_INFO, EMERGENCY_REVIEW, POLICY_CHECK,
DUPLICATE_CHECK, EXISTING_TICKET, CREATING_TICKET, UNKNOWN_COMMIT,
TICKET_OPEN, FINDING_SLOTS, AWAITING_SLOT_CONFIRMATION, BOOKING,
SCHEDULED, RESCHEDULING, DISPATCHED, STATUS_CONFLICT, IN_PROGRESS,
AWAITING_ACCEPTANCE, REWORK_REQUIRED, CANCELLED, ESCALATED, CLOSED
```

`UNKNOWN_COMMIT`, `STATUS_CONFLICT`, and `AWAITING_SLOT_CONFIRMATION` are
workflow conditions, not proposed ticket or appointment states.

## Candidate ticket-status matrix

Every listed transition requires an optimistic-lock check. In phase 3, every
committed transition must write its history plus an Outbox event in the same
transaction. Self-status operational changes, such as replacing an appointment
while a ticket remains `SCHEDULED`, still increment the ticket snapshot version
and record a ticket event.

| Current | Allowed next | Authorized trigger | Required conditions |
| --- | --- | --- | --- |
| `OPEN` | `ASSIGNED` | operator or deterministic matching service | eligible active worker selected |
| `OPEN` | `ESCALATED` | system or operator | emergency, policy conflict, insufficient evidence, or no worker |
| `OPEN` | `CANCELLED` | resident owner or operator | work has not started |
| `ASSIGNED` | `SCHEDULED` | application service after resident confirmation | a non-overlapping `BOOKED` appointment committed |
| `ASSIGNED` | `OPEN` | application service after worker rejection | assignment cleared and event recorded |
| `ASSIGNED` | `ESCALATED` | system or operator | repeated rejection or no valid slot |
| `ASSIGNED` | `CANCELLED` | resident owner or operator | work has not started |
| `SCHEDULED` | `IN_PROGRESS` | application service from a valid worker `STARTED` event | active appointment, valid event order, no status conflict |
| `SCHEDULED` | `ASSIGNED` | application service after worker cancellation | old appointment cancelled; re-matching required |
| `SCHEDULED` | `ESCALATED` | system or operator | arrival/status conflict or recovery failure |
| `SCHEDULED` | `CANCELLED` | resident owner or operator | cancellation policy passes and work has not started |
| `IN_PROGRESS` | `PENDING_ACCEPTANCE` | application service from worker `COMPLETED` | valid event order and completion evidence recorded |
| `IN_PROGRESS` | `ESCALATED` | system or operator | contradictory evidence or unsafe automatic continuation |
| `PENDING_ACCEPTANCE` | `CLOSED` | resident owner | explicit acceptance; closure invariants pass |
| `PENDING_ACCEPTANCE` | `REWORK_REQUIRED` | resident owner | explicit rejection with reason |
| `PENDING_ACCEPTANCE` | `ESCALATED` | operator or system | acceptance conflict or unresolved evidence |
| `REWORK_REQUIRED` | `ASSIGNED` | operator or deterministic matching service | rework cycle opened and worker selected |
| `REWORK_REQUIRED` | `ESCALATED` | operator or system | no worker, repeated failure, or policy conflict |
| `REWORK_REQUIRED` | `CANCELLED` | operator only | exceptional manual resolution with audit reason |
| `ESCALATED` | safe resume state or `CANCELLED`/`CLOSED` | operator only | explicit resolution, reason, current snapshot check, and all target invariants |
| `CANCELLED` | none | terminal | reopening creates an audited operator action and is deferred from release 1 |
| `CLOSED` | none | terminal | a materially new issue creates a new ticket |

### Ticket actor restrictions

- Residents may cancel their own eligible ticket, confirm a slot, accept work,
  or reject acceptance. They cannot assign workers, resolve escalation, or set
  worker progress.
- Workers contribute append-only events. They do not directly set ticket status.
- Operators may assign/reassign, escalate, resolve manual review, and perform
  exceptional cancellation/closure when invariants and audit requirements pass.
- System transitions occur only through deterministic application services.

### Open design point: `ESCALATED`

The candidate enum supplied by the user includes `ESCALATED`. Because escalation
is a manual hold rather than a natural lifecycle position, implementation needs
either an `escalated_from_status`/safe-resume field or a separate escalation
record. The recommendation for release 1 is to keep the requested status and
store `escalated_from_status`, but this choice requires explicit approval before
the enum and schema are implemented.

## Candidate appointment-status matrix

Each transition uses the appointment optimistic version, writes appointment
history, and in phase 3 writes an Outbox event in the same transaction.

| Current | Allowed next | Authorized trigger | Required behavior |
| --- | --- | --- | --- |
| `BOOKED` | `SUPERSEDED` | resident reschedule through application service | mark old appointment terminal, then create a new version/row; never overwrite its time |
| `BOOKED` | `CANCELLED_BY_RESIDENT` | resident owner | authorization and cancellation policy pass |
| `BOOKED` | `CANCELLED_BY_WORKER` | worker event or operator | worker identity/assignment validated; ticket returns for matching |
| `BOOKED` | `COMPLETED` | application service | worker completion sequence valid and ticket moved to acceptance |
| `BOOKED` | `NO_SHOW` | operator only | conflict investigated and outcome recorded |
| `SUPERSEDED` | none | terminal | replacement appointment references the superseded row |
| `CANCELLED_BY_RESIDENT` | none | terminal | later booking creates a new row |
| `CANCELLED_BY_WORKER` | none | terminal | replacement booking creates a new row |
| `COMPLETED` | none | terminal | resident acceptance affects the ticket, not the appointment |
| `NO_SHOW` | none | terminal | retry or rework uses a new appointment |

Candidate slots are not appointments. They stay in workflow state or a future
candidate-slot representation until the resident confirms one.

## Candidate worker-event rules

Worker events are immutable and append-only. Insertion requires actor/trace
identity and an idempotent source event key. The application service validates
event order and decides whether a domain transition follows.

| Event | Typical predecessor/evidence | Who may originate it |
| --- | --- | --- |
| `ACCEPTED` | active assignment and `BOOKED` appointment | assigned worker or operator simulation |
| `REJECTED` | active assignment before departure | assigned worker or operator simulation |
| `DEPARTED` | prior `ACCEPTED` | assigned worker or operator simulation |
| `ARRIVED` | prior `DEPARTED` | assigned worker or operator simulation |
| `STARTED` | prior `ARRIVED`, valid appointment, no conflict | assigned worker or operator simulation |
| `COMPLETED` | prior `STARTED`, ticket `IN_PROGRESS` | assigned worker or operator simulation |
| `CANCELLED` | active assignment before completion | assigned worker or operator simulation |
| `NO_SHOW` | conflicting arrival evidence reviewed | operator only |

An invalid or duplicate event remains traceable but does not advance ticket or
appointment state.

## Cross-aggregate invariants and conflicts

1. A ticket cannot be `SCHEDULED` without exactly one active `BOOKED` appointment.
2. A ticket cannot enter `IN_PROGRESS` without a valid `STARTED` event for its active appointment.
3. An appointment cannot become `COMPLETED` unless the ticket is or atomically becomes `PENDING_ACCEPTANCE`.
4. A ticket cannot become `CLOSED` without explicit resident acceptance.
5. A rejected acceptance moves the same ticket to `REWORK_REQUIRED`.
6. Worker `ARRIVED` versus resident no-show evidence stops automation, sets the
   workflow to `STATUS_CONFLICT`, and requires operator resolution; neither claim
   alone advances the ticket.
7. Database ticket/appointment state wins over workflow checkpoint state.
8. Stale versions fail rather than overwrite newer operator, resident, or worker actions.

## Rework recommendation

Release 1 should reuse the original ticket, increment `rework_count`, append a
rework event/history entry, and create a new appointment row. This preserves one
auditable problem lifecycle and makes the rejected acceptance measurable.

Do not create a child ticket for ordinary rework. A new ticket is appropriate
only when the resident reports a materially different category/location or a
new issue after closure. This recommendation also requires approval before the
week-1 schema is implemented.

