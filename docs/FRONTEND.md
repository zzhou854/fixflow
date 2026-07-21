# FixFlow Frontend

## Scope

The Task 9 UI is a Vite/React/TypeScript application using Ant Design as its
only component framework. Routes are `/login`, `/resident`, and `/operator`.
It is explicitly labelled **Development Demonstration Mode** because language
and embedding providers are deterministic offline demonstrations.

The resident page supports login/logout, authorised property selection, a new
or continued Agent conversation, live SSE status, the three typed interrupt
forms, and refreshed ticket/appointment summaries. Rescheduling and requests
for human handling use the Agent path. Cancellation and acceptance/rejection
remain property-staff operations in this first product slice.

The operator page provides ticket status/category/severity filters, business
details, ticket and appointment histories, and the latest Worker Event. A known
Agent thread can be inspected by the Operator Thread API only when its Agent
State ticket link is revalidated against the database resident/property ticket
snapshot. The UI never guesses a relation or fabricates policy, workflow, or
Trace data. Pre-ticket human-review discovery and Trace Runtime remain unavailable.

## Security boundary

Only the token and current `thread_id` are retained in `sessionStorage`; the
token is never put in a URL or log. On refresh `/auth/me` rebuilds the trusted
user/role, invalid tokens are removed, and Thread State is fetched again.
Passwords, complete Agent State, and policy evidence are not stored. SSE uses
authenticated `fetch` with a Bearer header and an `AbortController`, not native
`EventSource` or a query token. Reconnect performs another Thread State
reconciliation. The frontend never submits `actor_id` or `actor_type`, and route guards
are presentation only—the API enforces permission. React default escaping is
used; no `dangerouslySetInnerHTML` rendering exists.

## Local commands

Use Node.js 20 or newer:

```bash
cd frontend
npm ci
npm run dev
npm run lint
npm run typecheck
npm run test
npm run build
```

The default API is `http://127.0.0.1:8000`; set `VITE_API_BASE_URL` only for a
different local endpoint. Vite defaults to `http://127.0.0.1:5173`.

Vitest and React Testing Library cover login, safe errors, resident messages,
every interrupt control, ticket summary, role routing, and operator listing. A
heavy browser E2E framework is intentionally not introduced in Task 9.

The current Ant Design production chunk is about 1.06 MB before gzip. It is a
non-blocking first-release optimisation item; no second UI framework or complex
bundler plugin is introduced. API, MCP, and frontend still start as documented
separate processes, so one-command Compose delivery remains partial.
