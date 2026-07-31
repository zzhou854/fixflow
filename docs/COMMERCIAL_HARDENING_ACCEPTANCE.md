# Commercial hardening acceptance

Date: 2026-07-31

## Outcome

The four commercial-hardening phases are complete for the local scripted-provider
product:

1. system audit and backend reliability hardening;
2. resident product-shell and conversation redesign;
3. property-operator workbench and pre-ticket human-review queue;
4. real-browser vertical acceptance.

The default provider remains `scripted`. No online model was called, qualified,
activated, or presented as production-ready during this work. Independent
Holdout approval is still pending and is not implied by this acceptance.

## Resident acceptance

- The application uses a fixed `100vh` shell with independently scrolling
  conversation navigation and message history.
- The composer remains at the bottom of the chat workspace.
- The recent list is limited to five active conversations.
- The all-conversation drawer supports active, archived, and combined views.
- Archive is recoverable and does not cancel a ticket or appointment or delete
  Checkpoint, Trace, Replay, or audit history.
- A 390 × 844 viewport uses a mobile drawer instead of the desktop sidebar.
- Resident-facing workflow and issue labels are Chinese business language.
- Progress, retry, and property-staff handoff states remain visible instead of
  leaving an empty response area.

The real browser completed all three supported repair categories:

```text
electrical → create ticket → list slots → book appointment
water leak → create ticket → list slots → book appointment
door lock → create ticket → list slots → book appointment
```

## Operator acceptance

- A resident request for property-staff assistance before ticket creation
  produced a durable human-review case without fabricating a ticket.
- The operator queue exposed that case as an open task.
- The details view separates business summary, handling basis, and collapsed
  technical audit.
- Business reasons are localized; internal reason codes remain in technical
  audit.
- Ticket rows use localized category, priority, status, and appointment labels.

## Defects found and closed during browser acceptance

1. The all-conversation request used `limit=100` while the API contract permits
   at most 50. It now uses 50 and has a regression test.
2. The static archive confirmation did not render in the production browser.
   It is now a controlled component modal with a real confirmation-path test.
3. The archive filter label and loaded result set could disagree after archive
   or restore. The filter is now controlled and refreshes its current view.
4. A raw human-review reason code was visible in the business layer. It is now
   localized and the raw code is restricted to technical audit.

## Verification evidence

```text
Backend:
  Ruff format: 409 files
  Ruff check: passed
  Mypy: 409 source files, no issues
  pip check: no broken requirements
  Pytest: 1646 passed

Database:
  Alembic head/current: 20260730_0008
  Alembic drift: none
  PostgreSQL: healthy

Frontend:
  Test files: 14 passed
  Tests: 40 passed
  Lint: passed
  Typecheck: passed
  Production build: passed
  npm high-severity audit: 0 vulnerabilities

Containers:
  API: healthy
  MCP server: healthy
  PostgreSQL: healthy
  Frontend: rebuilt from the accepted source
```

Browser screenshots are stored outside the repository under the dated recovery
backup so they cannot be confused with production assets.

## Remaining non-product gates

- Independent human approval of the revised private Holdout Golden remains
  pending.
- Online-provider qualification, new locked Holdout execution, Shadow, Canary,
  and activation remain prohibited until their separate gates pass.
- The default provider remains `scripted`; the UI identifies the environment as
  a system demonstration rather than claiming an online model.
