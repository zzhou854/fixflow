# Proposed final state model

> Approval status: frozen by the user after the required semantic corrections.
> No enum, ORM field, migration, repository, or domain service is part of this
> documentation commit. Implementation starts only under the separate Task 2.

FixFlow separates four concepts:

- `workflow_stage` is recoverable orchestration progress;
- `ticket_status` is repair-ticket business truth in PostgreSQL;
- `appointment_status` is formal-appointment business truth in PostgreSQL;
- `worker_event_type` is immutable evidence about worker behavior.

Workflow checkpoints never override domain snapshots. Every resume rereads the
latest ticket, appointment, accepted worker-event sequence, versions, and
idempotency result before choosing the next node.

## Architecture review findings

1. Ticket `ASSIGNED` was not independently observable. Worker/slot selection is
   transient until a resident confirms a slot and an appointment is committed.
2. `PENDING_SCHEDULING` is unnecessary. `OPEN` plus workflow progress explains
   why a formal appointment does not yet exist.
3. Appointment cancellation statuses encoded the actor in the status name. A
   typed actor/subject/reason is clearer and avoids state growth.
4. Appointment `COMPLETED` confused visit completion, worker outcome, and resident
   acceptance. `FULFILLED` now means only that the visit occurred and ended.
5. A failed attempt after `STARTED` could not be recorded without abusing
   `CANCELLED`. `FAILED_TO_COMPLETE` is added as observable worker evidence and
   routes an ordinary retry to rework or an exceptional case to human review.
6. Workflow stages mirrored ticket statuses. They are replaced with orchestration
   stages such as `MONITORING_APPOINTMENT`, `HUMAN_REVIEW`, and `DONE`.
7. Invalid worker-event attempts must not pollute the canonical event stream. They
   create no domain mutation and are retained only in Trace/audit telemetry.

No state depends on LLM judgment. Permissions, transition legality, category or
location materiality, overlap, acceptance, and safe recovery are deterministic.

## Recommended final enums

### `ticket_status`

```text
OPEN
SCHEDULED
IN_PROGRESS
PENDING_ACCEPTANCE
REWORK_REQUIRED
ESCALATED
CANCELLED
CLOSED
```

- `OPEN`: valid ticket with no active formal appointment.
- `SCHEDULED`: exactly one active `BOOKED` appointment exists.
- `IN_PROGRESS`: a valid `STARTED` event exists for the active appointment.
- `PENDING_ACCEPTANCE`: a valid successful worker completion awaits the resident.
- `REWORK_REQUIRED`: resident rejection, or reviewed same-issue rework, requires a
  new appointment.
- `ESCALATED`: non-terminal manual hold with `escalated_from_status`.
- `CANCELLED`: terminal cancellation.
- `CLOSED`: terminal accepted completion; release 1 never reopens it.

`ASSIGNED` and `PENDING_SCHEDULING` are excluded. Pre-booking worker selection is
workflow/application data, not ticket business state.

### `appointment_status`

```text
BOOKED
FULFILLED
SUPERSEDED
CANCELLED
NO_SHOW
```

- `BOOKED` is the only active status. Arrival/start remain worker events.
- `FULFILLED` means the visit occurred and ended; it does not mean repair success
  or resident acceptance.
- `SUPERSEDED` means an atomic reschedule created a replacement row.
- `CANCELLED` uses typed actor, subject, and reason fields to distinguish causes.
- `NO_SHOW` uses a typed subject and reason to identify the absent party.

Candidate slots never create appointment rows. There is no appointment `PENDING`,
`IN_PROGRESS`, or automatic `EXPIRED`. All statuses except `BOOKED` are terminal.
Every formal appointment has a typed purpose: `INITIAL_REPAIR` or `REWORK`.
Purpose is immutable and never inferred from free text.

`FULFILLED`, `SUPERSEDED`, `CANCELLED`, and `NO_SHOW` are immutable terminal
statuses. They cannot reactivate, and their worker or core time interval cannot
be edited. Rescheduling and rework always create a new appointment. An audit note
may be appended, but it cannot overwrite the recorded status, actor, reason,
evidence, interval, or worker.

### Typed appointment outcome metadata

Generic `CANCELLED` and `NO_SHOW` do not hide who acted or why. Their persisted
database record and domain schema require:

```text
actor_type
actor_id
reason_code
reason_text
evidence
occurred_at
```

`reason_text` supplements rather than replaces `reason_code`; `evidence` contains
safe structured references. `NO_SHOW` reason codes distinguish at least
`RESIDENT_NO_SHOW` and `WORKER_NO_SHOW`. `CANCELLED` reason codes distinguish at
least `RESIDENT_CANCELLED`, `WORKER_CANCELLED`, `WORKER_REJECTED`,
`OPERATOR_CANCELLED`, and `SYSTEM_CANCELLED`. The appointment enum is not expanded
to encode those actors or reasons.

### `worker_event_type`

```text
ACCEPTED
REJECTED
DEPARTED
ARRIVED
STARTED
COMPLETED
FAILED_TO_COMPLETE
CANCELLED
NO_SHOW
```

Every event relates to one formal appointment and stores both
`subject_worker_id` and the real `recorded_by_actor_id`/role. Operator simulation
never impersonates a worker. `COMPLETED` is only the worker's completion claim;
it moves a ticket to acceptance and never directly closes it.

## Ticket transition matrix

Every accepted transition checks the ticket version and writes immutable status
history in the same transaction. Outbox is required in phase 3 once implemented.

| Current | Trigger | Next | Allowed roles | Preconditions | Expected version | History | Outbox | Failure behavior |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| none | create authorized ticket | `OPEN` | resident via service, operator | authorized property, valid issue, no disallowed duplicate, idempotency accepted | N/A | yes | phase 3 | reject or return idempotent existing result |
| `OPEN` | commit confirmed appointment | `SCHEDULED` | resident via service, operator | new non-overlapping `BOOKED` row commits atomically | yes | yes | phase 3 | roll back both aggregates |
| `OPEN` | cancel ticket | `CANCELLED` | resident owner, operator | work not started; no active appointment; reason | yes | yes | phase 3 | reject with no mutation |
| `OPEN` | escalate | `ESCALATED` | deterministic service, operator | typed reason/evidence; save prior status | yes | yes | phase 3 | reject with no mutation |
| `SCHEDULED` | valid worker `STARTED` | `IN_PROGRESS` | subject worker via service, operator simulation | active `BOOKED`; valid order and identities | yes | yes | phase 3 | reject event/transition; trace attempt |
| `SCHEDULED` | reschedule atomically | `SCHEDULED` | resident owner, operator | old row -> `SUPERSEDED`; one replacement `BOOKED` succeeds | yes | yes, scheduling event | phase 3 | whole transaction rolls back |
| `SCHEDULED` | worker `REJECTED` initial visit | `OPEN` | subject worker via service, operator simulation | valid event; appointment purpose `INITIAL_REPAIR` -> `CANCELLED`; reason `WORKER_REJECTED`; restart ordinary scheduling | yes | yes | phase 3 | reject event attempt; retain booking |
| `SCHEDULED` | worker `REJECTED` rework visit | `REWORK_REQUIRED` | subject worker via service, operator simulation | valid event; appointment purpose `REWORK` -> `CANCELLED`; reason `WORKER_REJECTED`; preserve rework count and restart rework scheduling | yes | yes | phase 3 | reject event attempt; retain booking |
| `SCHEDULED` | worker `CANCELLED` initial visit | `OPEN` | subject worker via service, operator simulation | valid event; appointment -> `CANCELLED`; reason `WORKER_CANCELLED`; initial cycle | yes | yes | phase 3 | reject event attempt; retain booking |
| `SCHEDULED` | worker `CANCELLED` rework visit | `REWORK_REQUIRED` | subject worker via service, operator simulation | valid event; appointment -> `CANCELLED`; reason `WORKER_CANCELLED`; rework cycle remains recorded | yes | yes | phase 3 | reject event attempt; retain booking |
| `SCHEDULED` | resident cancels appointment only | `OPEN` or `REWORK_REQUIRED` | resident owner, operator | policy passes; target depends on current cycle | yes | yes | phase 3 | reject with no mutation |
| `SCHEDULED` | cancel ticket | `CANCELLED` | resident owner, operator | work not started; appointment cancelled atomically; reason | yes | yes | phase 3 | roll back both aggregates |
| `SCHEDULED` | reviewed no-show/conflict | `ESCALATED` | operator, reconciliation service | appointment -> `NO_SHOW`; typed subject/evidence; save prior | yes | yes | phase 3 | stop automation; no mutation |
| `SCHEDULED` | escalate | `ESCALATED` | deterministic service, operator | typed reason/evidence; save prior status | yes | yes | phase 3 | reject with no mutation |
| `IN_PROGRESS` | `worker_event_type.COMPLETED` | `PENDING_ACCEPTANCE` | subject worker via service, operator simulation | valid order; appointment `BOOKED -> FULFILLED`; completion statement/evidence | yes | yes | phase 3 | reject event and both transitions |
| `IN_PROGRESS` | ordinary `worker_event_type.FAILED_TO_COMPLETE` | `REWORK_REQUIRED` | subject worker via service, operator simulation | retry remains schedulable; appointment `BOOKED -> FULFILLED`; typed reason, evidence, worker statement; increment rework count/history | yes | yes | phase 3 | reject event and both transitions |
| `IN_PROGRESS` | exceptional `worker_event_type.FAILED_TO_COMPLETE` | `ESCALATED` | subject worker via service, operator simulation | safety risk, responsibility conflict, special resource, or indeterminate classification; appointment `BOOKED -> FULFILLED`; typed reason, evidence, worker statement; save prior | yes | yes | phase 3 | reject event; stop automation |
| `IN_PROGRESS` | escalate conflict/safety issue | `ESCALATED` | deterministic service, operator | typed reason/evidence; save prior status | yes | yes | phase 3 | reject with no mutation |
| `PENDING_ACCEPTANCE` | resident accepts | `CLOSED` | resident owner | explicit acceptance; no unresolved conflict | yes | yes | phase 3 | reject/stale; never auto-close |
| `PENDING_ACCEPTANCE` | resident rejects | `REWORK_REQUIRED` | resident owner | reason; increment count and append rework record | yes | yes | phase 3 | reject with no mutation |
| `PENDING_ACCEPTANCE` | escalate conflict | `ESCALATED` | deterministic service, operator | typed evidence; save prior status | yes | yes | phase 3 | stop automation |
| `REWORK_REQUIRED` | commit rework appointment | `SCHEDULED` | resident via service, operator | same issue; new `BOOKED`; rework record exists | yes | yes | phase 3 | roll back both aggregates |
| `REWORK_REQUIRED` | exceptional cancellation | `CANCELLED` | operator | typed resolution; no active appointment; policy permits | yes | yes | phase 3 | reject with no mutation |
| `REWORK_REQUIRED` | escalate | `ESCALATED` | deterministic service, operator | typed reason/evidence; save prior status | yes | yes | phase 3 | reject with no mutation |
| `ESCALATED` | resume without booking | `OPEN` | operator | prior `OPEN`/`SCHEDULED`; no active appointment; resolution | yes | yes | phase 3 | remain escalated |
| `ESCALATED` | resume scheduled visit | `SCHEDULED` | operator | exactly one valid `BOOKED`; no conflict | yes | yes | phase 3 | remain escalated |
| `ESCALATED` | resume active work | `IN_PROGRESS` | operator | prior `IN_PROGRESS`; valid `STARTED`; no terminal event | yes | yes | phase 3 | remain escalated |
| `ESCALATED` | resume acceptance | `PENDING_ACCEPTANCE` | operator | valid `COMPLETED`, `FULFILLED`, no conflict | yes | yes | phase 3 | remain escalated |
| `ESCALATED` | approve rework | `REWORK_REQUIRED` | operator | reviewed same-issue evidence; rework count/history updated | yes | yes | phase 3 | remain escalated |
| `ESCALATED` | resolve accepted closure | `CLOSED` | operator applying resident decision | explicit authorized resident acceptance already exists | yes | yes | phase 3 | operator cannot replace resident acceptance |
| `ESCALATED` | resolve cancellation | `CANCELLED` | operator | work not started or reviewed rework termination; no booking | yes | yes | phase 3 | remain escalated |
| `CANCELLED` | ordinary mutation | none | none | terminal | N/A | no | no | reject as terminal |
| `CLOSED` | ordinary mutation | none | none | terminal; new problem creates new ticket | N/A | no | no | reject as terminal |

### `ESCALATED` entry and recovery

The complete escalation-entry set is:

```text
OPEN
SCHEDULED
IN_PROGRESS
PENDING_ACCEPTANCE
REWORK_REQUIRED
```

`CANCELLED` and `CLOSED` can never enter `ESCALATED`. Entry atomically saves the
prior status. Repeated escalation is an idempotent operation/event, not a
self-transition.

Recovery never accepts an arbitrary target. The service derives the allowed
target only after rereading the latest PostgreSQL ticket, appointment, accepted
event sequence, resident decision, rework record, manual disposition, and all
versions. The saved `escalated_from_status` participates in that derivation; the
caller cannot supply a free target. The explicit rows above are the complete
release-1 target set. Failure leaves the ticket escalated. Success clears the
current-row prior-status field; immutable history preserves it. An operator
cannot substitute for resident acceptance.

## Appointment transition matrix

Appointment interval/worker fields are immutable. Each existing-row transition
checks its version and writes history; creation checks the owning ticket version.

| Current | Trigger | Next | Allowed roles | Preconditions | Expected version | New appointment | Old appointment | Outbox |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| none | book confirmed slot | `BOOKED` | resident via service, operator | ticket `OPEN`/`REWORK_REQUIRED`; eligible worker/interval; no overlaps | ticket yes; appointment N/A | yes | none | phase 3 |
| `BOOKED` | reschedule | `SUPERSEDED` | resident owner, operator | confirmed conflict-free replacement on same ticket | ticket and old row yes | yes, atomically | immutable; replacement uniquely references it | phase 3 |
| `BOOKED` | cancel | `CANCELLED` | resident owner, subject worker via service, operator/system | authorization/policy; required actor ID/type, reason code/text, evidence, occurred time; ticket adjusted | ticket and row yes | no | terminal; later booking is new row | phase 3 |
| `BOOKED` | successful visit end | `FULFILLED` | subject worker via service, operator simulation | valid `COMPLETED` after `STARTED`; ticket -> acceptance | ticket and row yes | no | terminal; acceptance affects ticket only | phase 3 |
| `BOOKED` | ordinary unsuccessful visit end | `FULFILLED` | subject worker via service, operator simulation | valid `FAILED_TO_COMPLETE`; retry remains schedulable; ticket -> `REWORK_REQUIRED` | ticket and row yes | no | terminal; continuation uses new row | phase 3 |
| `BOOKED` | exceptional unsuccessful visit end | `FULFILLED` | subject worker via service, operator simulation | valid `FAILED_TO_COMPLETE`; safety/conflict/special-resource/indeterminate case; ticket -> `ESCALATED` | ticket and row yes | no | terminal; continuation requires review and new row | phase 3 |
| `BOOKED` | reviewed no-show | `NO_SHOW` | operator, reconciliation service | window reached; required actor ID/type, resident/worker reason code, reason text, evidence, occurred time | ticket and row yes | no | terminal; retry uses new row | phase 3 |
| `FULFILLED` | any mutation | none | none | terminal | N/A | no | immutable | no |
| `SUPERSEDED` | any mutation | none | none | terminal | N/A | no | immutable; cannot reactivate | no |
| `CANCELLED` | any mutation | none | none | terminal | N/A | no | immutable | no |
| `NO_SHOW` | any mutation | none | none | terminal | N/A | no | immutable | no |

Time passing alone does not create `EXPIRED` or `NO_SHOW`. A deterministic
reconciliation step plus reviewed evidence records `NO_SHOW` or escalates.

## Worker-event rule matrix

Canonical events store appointment/ticket IDs, subject worker, real recording
actor/role/source, occurred/recorded times, per-appointment sequence, typed reason
and evidence, trace ID, source key, and request hash.

| Event | Allowed roles | Required ticket | Required appointment | Ticket effect | Appointment effect | Duplicate allowed | Illegal sequence behavior |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ACCEPTED` | subject worker, operator simulation | `SCHEDULED` | `BOOKED` | none | none | no; same key/hash returns existing | reject; trace attempt; no canonical event |
| `REJECTED` | subject worker, operator simulation | `SCHEDULED` | `BOOKED` | initial purpose -> `OPEN`; rework purpose -> `REWORK_REQUIRED`; count unchanged | -> `CANCELLED`; actor worker; reason `WORKER_REJECTED` | idempotent replay only | only before `ACCEPTED`; require typed purpose consistent with rework count; never reactivate |
| `DEPARTED` | subject worker, operator simulation | `SCHEDULED` | `BOOKED` | none | none | idempotent replay only | require `ACCEPTED` |
| `ARRIVED` | subject worker, operator simulation | `SCHEDULED` | `BOOKED` | none | none | idempotent replay only | require `DEPARTED` |
| `STARTED` | subject worker, operator simulation | `SCHEDULED` | `BOOKED` | -> `IN_PROGRESS` | remains `BOOKED` | idempotent replay only | require `ARRIVED`, versions, no conflict |
| `COMPLETED` | subject worker, operator simulation | `IN_PROGRESS` | `BOOKED` | -> `PENDING_ACCEPTANCE` | -> `FULFILLED` | idempotent replay only | require `STARTED`; reject terminal contradiction |
| `FAILED_TO_COMPLETE` | subject worker, operator simulation | `IN_PROGRESS` | `BOOKED` | ordinary retry -> `REWORK_REQUIRED`; exceptional/indeterminate -> `ESCALATED` | -> `FULFILLED` | idempotent replay only | require `STARTED`, typed reason, evidence, worker statement |
| `CANCELLED` | subject worker, operator simulation | `SCHEDULED` | `BOOKED` | initial -> `OPEN`; rework -> `REWORK_REQUIRED`; count unchanged | -> `CANCELLED`; reason `WORKER_CANCELLED` | idempotent replay only | require prior `ACCEPTED`; only before `STARTED`; otherwise outcome event |
| `NO_SHOW` | operator/reconciliation; subject is assigned worker | `SCHEDULED` | `BOOKED` | -> `ESCALATED` | -> `NO_SHOW` | idempotent replay only | require reviewed evidence/window |

Formal assignment is the committed appointment plus its worker. Matching
candidates are not worker events. Sequence uses a transactionally allocated
unique `(appointment_id, sequence_no)` plus predecessor rules; client timestamps
never determine order alone. Unique `(source, source_event_key)` enforces
idempotency. Same key/different hash is a conflict.

Release 1 has no separate `worker_assignment` entity. `ACCEPTED` must reference a
`BOOKED` appointment and means only that its assigned worker confirmed the
appointment; neither aggregate changes status, and an identical replay is
idempotent. `REJECTED` must also reference a `BOOKED` appointment. It atomically
changes that appointment to `CANCELLED`, persists
`cancelled_by_actor_type=WORKER`, typed reason `WORKER_REJECTED`, explanation and
evidence. An `INITIAL_REPAIR` appointment returns the ticket from `SCHEDULED` to
`OPEN`; a `REWORK` appointment returns it to `REWORK_REQUIRED` without changing
`rework_count`. If replacement scheduling later fails, a separate deterministic
command escalates the ticket. The rejected appointment never reactivates.

For residents, `SCHEDULED` means the system has reserved a time and worker, not
that the worker can no longer reject it. A rejection must be surfaced as active
rescheduling in the future UI; the system must not hide that change.

## Workflow stages

```text
INTAKE
NEED_PROPERTY
NEED_INFO
EMERGENCY_REVIEW
POLICY_CHECK
DUPLICATE_CHECK
EXISTING_TICKET
CREATING_TICKET
UNKNOWN_COMMIT
FINDING_SLOTS
AWAITING_SLOT_CONFIRMATION
BOOKING
MONITORING_APPOINTMENT
RESCHEDULING
STATUS_CONFLICT
AWAITING_ACCEPTANCE
PLANNING_REWORK
HUMAN_REVIEW
DONE
```

`workflow_stage` may be finer-grained than domain state but is never a business
entity's only truth. `DONE` has a separate workflow outcome (`CLOSED`,
`CANCELLED`, or `HANDED_OFF`) and is not a ticket status.

## Workflow/domain consistency map

| Workflow stage | Allowed ticket | Allowed appointment | Entry condition | Reread on resume |
| --- | --- | --- | --- | --- |
| `INTAKE` | none or non-terminal | none or any | new/corrected message | authorization, linked tickets, current aggregate versions |
| `NEED_PROPERTY` | none | none | property identity/authorization incomplete | resident-property relations, input version |
| `NEED_INFO` | none or `OPEN` | none | structured issue data incomplete | ticket/version if present, authorization |
| `EMERGENCY_REVIEW` | none, `OPEN`, `ESCALATED` | none or `BOOKED` | deterministic safety rule | ticket/version, evidence, appointment, escalation |
| `POLICY_CHECK` | none or `OPEN` | none | structured request complete | policy version, authorization, ticket |
| `DUPLICATE_CHECK` | none | none | policy permits creation | current open tickets for property/category/location |
| `EXISTING_TICKET` | any | none or any | existing ticket selected | ticket, active appointment, versions, events |
| `CREATING_TICKET` | none | none | mutation dispatched/not reconciled | idempotency record and duplicate query |
| `UNKNOWN_COMMIT` | none or any | none or any | mutation outcome uncertain | idempotency result, snapshot/version, history/outbox |
| `FINDING_SLOTS` | `OPEN`, `REWORK_REQUIRED` | terminal or none | ticket needs booking | ticket/version, eligibility, availability, active query |
| `AWAITING_SLOT_CONFIRMATION` | `OPEN`, `REWORK_REQUIRED` | terminal or none | transient candidates returned | ticket/version, candidate expiry, availability |
| `BOOKING` | `OPEN`, `REWORK_REQUIRED`, `SCHEDULED` | none/terminal or `BOOKED` | resident confirmed slot | versions, idempotency, overlap result |
| `MONITORING_APPOINTMENT` | `SCHEDULED`, `IN_PROGRESS`, `ESCALATED` | `BOOKED` or resulting terminal | booking/visit exists | ticket, appointment, versions, event sequence |
| `RESCHEDULING` | `SCHEDULED`, `ESCALATED` | `BOOKED` or `SUPERSEDED` plus replacement `BOOKED` | reschedule requested | ticket, old/new rows, versions, overlaps |
| `STATUS_CONFLICT` | any non-terminal | any | claims/snapshots conflict | all aggregates, versions, events, evidence, idempotency |
| `AWAITING_ACCEPTANCE` | `PENDING_ACCEPTANCE`, `ESCALATED` | `FULFILLED` | valid successful completion | ticket/version, visit, evidence, resident decision |
| `PLANNING_REWORK` | `REWORK_REQUIRED`, `SCHEDULED`, `ESCALATED` | terminal or new `BOOKED` | rejection/reviewed rework | ticket/version/count/history, appointments, eligibility |
| `HUMAN_REVIEW` | `ESCALATED` | any | escalation/reconciliation block | prior/current status, all versions/events/evidence/permissions |
| `DONE` | `CLOSED`, `CANCELLED`, handed-off `ESCALATED` | terminal or none | no automatic next action | final snapshots, versions, terminal history/outcome |

Any mapping violation moves workflow to conflict/review; it never rewrites the
database to match a checkpoint.

## Complete rework flow

1. `worker_event_type.COMPLETED` means the worker declares this repair attempt
   ended. In one transaction it changes `appointment_status` from `BOOKED` to
   `FULFILLED` and `ticket_status` from `IN_PROGRESS` to `PENDING_ACCEPTANCE`.
   It never closes the ticket; only an authorized resident acceptance can do so.
2. Resident rejection with a structured reason moves the same ticket to
   `REWORK_REQUIRED`, increments `rework_count`, and appends immutable history.
3. Candidate rework slots remain transient.
4. Confirmation creates a new `BOOKED` row and moves the ticket to `SCHEDULED`.
5. Prior appointments remain immutable and the normal visit/acceptance loop repeats.
6. A new ticket is used only for a material category/location change or a new
   problem after closure.

`worker_event_type.FAILED_TO_COMPLETE` also changes the appointment from `BOOKED`
to `FULFILLED`. An ordinary, schedulable cause changes the ticket from
`IN_PROGRESS` to `REWORK_REQUIRED`. A safety risk, responsibility conflict,
special-resource need, or indeterminate cause changes it to `ESCALATED`. Both
paths save the typed failure reason, evidence, and worker statement. Human review
may later approve same-issue rework from escalation.

## Complete reschedule flow

1. Request uses current ticket and appointment versions.
2. Candidate slots stay transient; the old `BOOKED` row stays active while choosing.
3. Confirmation rechecks authorization, policy, eligibility, and overlaps.
4. One transaction marks the old row `SUPERSEDED`, creates a new `BOOKED` row that
   uniquely references it, writes histories, and records a versioned
   `SCHEDULED -> SCHEDULED` event.
5. Failure rolls back everything; the old booking stays active.
6. A superseded appointment never reactivates.

Historical rows are intentional audit evidence, not meaningless data inflation.

## Business invariants

1. A ticket has at most one active `BOOKED` appointment.
2. A worker has no overlapping active appointments.
3. A ticket cannot close before `PENDING_ACCEPTANCE` and resident acceptance.
4. Worker `COMPLETED` cannot directly close a ticket.
5. Resident rejection always enters same-ticket rework.
6. `CLOSED` accepts no ordinary repair events or appointments.
7. `CANCELLED` accepts no appointments or ordinary worker events.
8. Replaced appointments cannot reactivate.
9. Every terminal appointment is immutable.
10. Repeated mutations are idempotent and never transition twice.
11. Version mismatch causes no aggregate/history/event update.
12. Escalation recovery must pass the full state machine.
13. Aggregate/event conflicts stop automatic progression.
14. PostgreSQL wins over Agent State/checkpoint.
15. Residents act only on authorized properties/tickets.
16. Every accepted transition writes immutable history in the same transaction.
17. Outside `ESCALATED`, `SCHEDULED` iff exactly one active `BOOKED` row exists;
    an escalated ticket may retain one booking while automation is paused.
18. `IN_PROGRESS` requires valid `STARTED` evidence.
19. `PENDING_ACCEPTANCE` requires valid `COMPLETED` and `FULFILLED` evidence.
20. Actor, subject, trace, key, and request hash never come from LLM free text.
21. `FAILED_TO_COMPLETE` always ends the current appointment and never closes the
    ticket; its typed classification determines rework versus escalation.
22. Terminal appointment facts are immutable; audit annotations are append-only.
23. Appointment purpose and `rework_count` must agree; rejecting or cancelling a
    rework appointment preserves the existing count and rework semantics.

## Database constraint candidates

Recommendations only; this task creates no migration.

| Concern | Recommendation | Trade-off / service rule |
| --- | --- | --- |
| Enum storage | Python string Enum plus `VARCHAR` and named `CHECK` | Easier reversible evolution than Native Enum; tests keep values synchronized |
| Version | non-null `BIGINT` default 1, `CHECK (version >= 1)`; update predicate includes old value | Service maps zero updated rows to conflict |
| Idempotency | unique `(operation_scope, actor_id, idempotency_key)` plus request hash/result | same key/different hash is conflict |
| One active appointment | partial unique index on `ticket_id WHERE status='BOOKED'` | service changes both aggregates atomically |
| Worker overlap | GiST exclusion on worker and `[starts_at, ends_at)` for `BOOKED`; `btree_gist` | service returns friendly conflict details |
| Interval | `CHECK (starts_at < ends_at)` | business hours stay in service |
| History | non-null aggregate/actor/trace references; no cascade delete | transition legality stays in domain service |
| Escalation prior | check that status is `ESCALATED` iff prior is non-null; prior excludes terminal/escalated | safe target is cross-aggregate service logic |
| Rework count | non-null default 0, `CHECK (rework_count >= 0)` | count and history insert are atomic |
| Supersession | nullable unique `supersedes_appointment_id` FK on replacement | same ticket/no cycle needs service or deferred trigger |
| Cancellation | required `actor_type`, `actor_id`, `reason_code`, `reason_text`, `evidence`, `occurred_at`; reason-code CHECK includes the approved cancellation codes | authorization and allowed reason for context stay in service |
| No-show | same required outcome fields; reason-code CHECK distinguishes `RESIDENT_NO_SHOW`/`WORKER_NO_SHOW` | evidence adjudication and actor authorization stay in service |
| Acceptance | immutable decision/history with actor, reason, evidence, version | authorization/transition stays in service |
| Event order | unique `(appointment_id, sequence_no)` and `(source, source_event_key)` | predecessor legality stays in domain service |

Database constraints enforce local facts and concurrency. Domain services enforce
authorization, transitions, cross-aggregate atomicity, safe recovery, material
issue changes, policy, event predecessors, and resident acceptance.

## Approval record and implementation gate

The user approved the final enums, matrices, workflow stages, string-plus-CHECK
storage strategy, escalation model, rework model, and rescheduling model. This
commit freezes that design and contains documentation only. Enum, domain-model,
test, ORM, and migration implementation remains forbidden until the user starts
“Task 2: implement the pure domain state model and transition tests.”
