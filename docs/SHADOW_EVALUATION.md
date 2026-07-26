# LLM shadow evaluation

FixFlow's product runtime always uses the deterministic `scripted` provider for
business decisions. An online provider is not qualified and cannot be selected
as the primary product provider.

Shadow evaluation is disabled by default:

```text
FIXFLOW_LLM_PROVIDER=scripted
FIXFLOW_LLM_EXPERIMENTAL_ENABLED=false
FIXFLOW_LLM_EXPERIMENTAL_PROVIDER=deepseek
```

When an operator explicitly enables the internal flag, the product still runs
the scripted interpretation first and returns that result immediately. A
background observer sends the same bounded, sanitized interpretation request
to the configured experimental provider. Its result is never returned to the
resident and cannot change routing, state, authorization, policy decisions,
MCP calls, idempotency, checkpoints, traces, outbox records, tickets, or
appointments.

The task-closure implementation stores only a bounded process-local record:
status, provider/model identity, prompt/schema identity, latency, attempt count,
token counts, and a safe exception type. It stores no message, prompt, provider
payload, business identifier, credential, or raw error. Process restart clears
these records. This is an evaluation facility, not a business fact source.

Provider failure is isolated from the scripted result. Shutdown drains pending
observations and closes the experimental provider. The client cannot enable
shadow mode through an API request.

No online provider is activated by this feature. Production activation remains
a separate decision after formal qualification and reviewed real-distribution
evidence.
