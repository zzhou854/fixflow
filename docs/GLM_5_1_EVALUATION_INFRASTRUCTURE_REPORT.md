# GLM-5.1 evaluation infrastructure evidence

## Decision

| Field | Value |
| --- | --- |
| Execution status | `EVALUATION_BLOCKED_INFRASTRUCTURE` |
| Prompt quality decision | `INCONCLUSIVE` |
| Prompt | `resident_interpretation@2.0.0` |
| Formal qualification | `NOT_STARTED` |
| Baseline | `NOT_CREATED` |
| Release Candidate | `NOT_CREATED` |
| Activation | `NOT_ACTIVATED` |
| Default Provider | `scripted` |

This evidence does not classify GLM-5.1 model quality. No complete Development
Repeat exists after the scheduler repair.

## Scheduler repair

Commit `85768cd` added an evaluation-only scheduler with single concurrency,
requests-per-minute pacing, trusted `Retry-After`, bounded exponential backoff,
finite jitter, whole-run circuit pauses, strict infrastructure-only retry and
identity-bound Resume. Invalid JSON, Schema, invariant, matcher and critical
boundary failures are never retried.

The historical Task-16 execution is now
`EVALUATION_BLOCKED_INFRASTRUCTURE`, with quality `INCONCLUSIVE`.

## Controlled probes

The first post-repair probe used 20 requests/minute and bounded five attempts.
Its first Case ended `RATE_LIMITED`; the run was stopped before a second Case
to avoid waste.

The second independent probe used:

| Setting | Value |
| --- | ---: |
| Requests per minute | 5 |
| Minimum interval | 12 seconds |
| Maximum Provider attempts | 3 |
| Initial / maximum backoff | 15 / 60 seconds |
| Consecutive rate-limit threshold | 2 |
| Circuit pause | 120 seconds |
| Maximum circuit pauses | 1 |

Run ID: `a4fa25c8-5529-45c5-9f6e-4f609b432377`.

The first Case again ended `RATE_LIMITED` after all three attempts. Total
audited waiting was 165.677823 seconds, including one whole-run circuit pause.
The upstream response supplied no usable `Retry-After`. Probe completion was
0%, so Smoke and all quality stages were correctly skipped.

After an additional cooldown, a third isolated `probe-only` run lowered the
rate to one request per minute. Run
`6f50365f-015d-434a-b1e2-846395411394` again ended its first Case
`RATE_LIMITED` after two bounded attempts and 59.797 seconds of audited waiting.
Its safe summary hash is
`c54537115f867ae538effee7aa992e77d3a3b3369deb9fc072c78e2cf69b7240`.
This rules out concurrency and request bursts as the current cause and is
consistent with an account-level quota or upstream service limit.

Safe evidence hashes:

- development summary:
  `63203ad18fd80dd10181662d8e51e82724b51bf6cdacd3eca677ba3d94f54466`
- development gate:
  `2b7497b2581d98ff4d609d8ce1c573770f11992220f2aab1c3430fd88ee9f3e5`
- stability status:
  `ad09adea00cc6c8ea232b15d9ed1b5501e8eece707ac97d1d8c9d8863564cd79`

## Boundaries

Challenge live calls were zero. No Agent Graph, MCP call, business mutation,
Outbox, Checkpoint, Replay, business Trace or business database write occurred.
No credential, raw SDK response, HTTP header, full Prompt or full input is
retained in this report.

This is currently an upstream quota/rate-limit blocker, not
`GLM-5.1_MODEL_CAPABILITY_BLOCKER`. Prompt iteration and formal requalification
must resume only after a conservative Probe completes successfully.
