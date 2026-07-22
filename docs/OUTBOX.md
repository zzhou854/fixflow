# Transactional Outbox

FixFlow records externally observable business changes in `outbox_events` in
the same PostgreSQL transaction as the aggregate write, history row, and
request-idempotency result. A rollback therefore removes all four effects; a
successful replay returns the stored result without creating another event.

## Closed event set

The first release emits `ticket.created`, `ticket.status_changed`,
`appointment.booked`, `appointment.rescheduled`, and `ticket.escalated`.
Payloads are typed, minimal business summaries. They do not contain passwords,
tokens, full conversations, checkpoint state, SQL, or provider prompts.

The event key is a SHA-256 digest of event type, aggregate ID, aggregate
version, and operation ID. The operation ID is derived from the command scope,
actor, and request idempotency key when the trusted caller does not supply one.
Time, random trace IDs, and delivery-attempt numbers never affect this key.

## Delivery

Run the dedicated worker with:

```powershell
uv run python -m app.outbox.run
```

Workers claim bounded batches with PostgreSQL `FOR UPDATE SKIP LOCKED`, commit
the short claim transaction, then call the consumer without holding the row
lock. Every claim receives a new server-generated UUID `claim_token`, including
after an expired lease is reclaimed. Acknowledgement, retry, and dead-letter
updates use `(event_id, PROCESSING, claimed_by, claim_token)` as a conditional
fence; an old worker receives `STALE_OUTBOX_CLAIM` and cannot change a newer
claim or a dispatched row. The token is neither a business event key nor part
of an event/trace payload.

The closed delivery state machine is `PENDING → PROCESSING → DISPATCHED`, with
`PROCESSING → PENDING` for a retry and `PROCESSING → DEAD_LETTER` after the
configured limit. Expired `PROCESSING` rows may only become a new
`PROCESSING` claim with a fresh token. Database constraints require all lease
fields exactly while processing and require `dispatched_at` exactly when
dispatched.

A failure increments `attempt_count`, records a truncated safe error, and uses
exponential backoff. Exhausted events enter `DEAD_LETTER`; poison events do not
block later rows.

Delivery is **at least once**, not exactly once. `TraceDomainEventProjector`
derives its trace-event key from the Outbox key, so a consumer write followed
by a lost or fenced acknowledgement is safe to retry: the next worker can
project idempotently and acknowledge its own claim. Outbox is not a general
message-bus framework and no Redis, Kafka, or Celery dependency is introduced.

## Configuration

`FIXFLOW_OUTBOX_POLL_INTERVAL_SECONDS`, `FIXFLOW_OUTBOX_BATCH_SIZE`,
`FIXFLOW_OUTBOX_LEASE_SECONDS`, `FIXFLOW_OUTBOX_MAX_ATTEMPTS`, and
`FIXFLOW_OUTBOX_RETRY_BASE_SECONDS` are validated by the shared Settings model.

Task 11 consumes Outbox as one required authoritative commit witness. Delivery
status alone never proves a business result and the reconciliation worker never
republishes a mutation. Replay remains Task 12 work.

Each reconciled action has an explicit required event: `ticket.created`,
`appointment.booked`, `appointment.rescheduled`, or `ticket.escalated`. An
unrelated/missing event, wrong aggregate version, or conflicting canonical result
is INCONSISTENT rather than COMMITTED.
