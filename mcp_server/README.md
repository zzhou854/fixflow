# property-operations-mcp

This package is the independent Streamable HTTP MCP server for FixFlow's
deterministic property operations. It exposes eight typed tools and delegates
all authorization, transaction, idempotency, optimistic-lock, and domain-state
logic to the existing Application layer.

Run it from the repository root:

```powershell
uv run python -m mcp_server
```

Configuration comes from `MCP_HOST`, `MCP_PORT`, and `DATABASE_URL`; the default
endpoint is `http://127.0.0.1:8765/mcp`. See `docs/MCP_CONTRACTS.md` for the tool
schemas, result mapping, trust boundary, and candidate-slot rules.
