# Deterministic fault injection

Production composition uses `NoOpFaultInjector`. Tests may explicitly inject a
`ScriptedFaultInjector` keyed by operation ID, fixed fault point, and one-based
hit count. There is no HTTP route, JWT claim, frontend switch, or production
environment flag that enables faults.

The closed points cover before/after MCP send, server commit before response,
response receipt before validation, validated result before Checkpoint,
reconciliation query boundaries, and resolution commit before acknowledgement.
Actions are timeout, connection reset, malformed response, controlled exception,
and process-termination barrier. A before-send fault is `NOT_SENT`; faults after
possible delivery are `UNKNOWN_COMMIT`. The harness creates deterministic test
evidence and never changes business rules. Operator escalation is deliberately
not routed through MCP. Its equivalent transport-neutral hooks are
`BEFORE_MUTATION_DISPATCH`, `AFTER_MUTATION_DISPATCH`,
`AFTER_COMMIT_BEFORE_RESULT`, `AFTER_RESULT_RECEIVED_BEFORE_VALIDATION`, and
`AFTER_RESULT_VALIDATED_BEFORE_RUN_FINALIZATION`.

The eight production boundary hooks are `BEFORE_MCP_SEND`, `AFTER_MCP_SEND`,
`AFTER_SERVER_COMMIT_BEFORE_RESPONSE`,
`AFTER_RESPONSE_RECEIVED_BEFORE_VALIDATION`,
`AFTER_RESULT_VALIDATED_BEFORE_CHECKPOINT`, `BEFORE_RECONCILIATION_QUERY`,
`AFTER_RECONCILIATION_QUERY_BEFORE_RESOLUTION`, and
`AFTER_RESOLUTION_COMMIT_BEFORE_ACK`. Production composition never accepts a
request flag and always defaults to `NoOpFaultInjector`; only test composition
can inject a scripted implementation.

Real vertical tests cover a committed ticket whose MCP response is lost, a
rolled-back booking whose response classification is lost and then safely
retries with the original operation/key, and contradictory evidence that becomes
manual review. Repository tests cover expired-lease reclaim, stale-token fencing,
and acknowledgement loss after resolution without a second terminal Trace.
Additional real API/PostgreSQL cases cover Operator escalation after commit
response loss, rollback followed by same-key continuation, and contradictory
evidence routed to manual review.
