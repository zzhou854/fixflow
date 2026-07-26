# Security release audit

## Result

The scripted-provider release audit passed with no known High or Critical npm
advisories. The pre-remediation audit found seven High advisories; the final
audit found zero vulnerabilities.

## Dependency remediation

React Router was upgraded to the official fixed major line (`8.3.0`) and imports
were migrated to `react-router`. React/React DOM, ESLint, related type packages,
and the vulnerable transitive `brace-expansion` path were upgraded through
reviewed dependency changes. No `npm audit fix --force` or vulnerability
suppression was used.

Frontend regression evidence: Lint passed, Typecheck passed, 29 tests passed,
production build passed, and `npm audit --audit-level=high` reported zero.

## Runtime boundaries

- JWT uses HS256 and a configured secret of at least 32 bytes; placeholder,
  missing, short, expired, invalid-algorithm, invalid-signature, missing-claim,
  and inactive-user cases fail closed.
- CORS and Trusted Hosts use explicit allowlists. CORS is not authorization.
- Resident/Operator permissions are checked against database identity and
  ownership; request bodies cannot replace caller identity.
- SSE uses a Bearer header through Fetch Streaming; tokens never enter URLs.
- API routers do not access ORM, repositories, MCP transport, Checkpoints, or
  state-machine assignments.
- Containers exclude `.env`, artifacts, caches, virtual environments,
  `node_modules`, and built frontend output from build context.
- Product containers run as non-root.
- The unqualified online model cannot become the production primary provider
  and Shadow mode is forced off in production.

## Secrets

`.env` is ignored and was not staged. `.env.example` contains placeholders
only. Release scanning found no committed API key, Bearer token, private key,
or credential-bearing database URL. Online raw evaluation artifacts are
gitignored and removed after safe reports are committed.

## Accepted non-blocking items

The Ant Design production bundle is approximately 1.15 MB before gzip. This is
a performance optimization item, not a security exception. No High/Critical
release exception remains.
