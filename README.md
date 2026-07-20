# Performance AI — Off-Path OTel POC

Performance AI is a proof-of-concept observability layer for agentic AI
systems, built for Imperative AI (Rick Barnum). It demonstrates passive
runtime telemetry plus customer-approved agent-semantic instrumentation,
collected off the production request path and correlated into a single
observability plane.

## Folder map

- **`frontend/`** — the chat UI the demo user interacts with.
- **`backend/`** — the API, LangGraph orchestrator, and agents. This is
  the most complete part of the repo so far — see
  [`backend/README.md`](backend/README.md) for its full contract, ID
  scheme, and known limitations.
- **`mcp-server/`** — the MCP server and its mock tools/data.
- **`otel/`** — the agent-semantic instrumentation layer and event
  schemas.
- **`infra/`** — Collector configuration and dashboards.
- **`docs/`** — architecture design documents.

## Status

`backend/` is complete and documented for its current stage (API
skeleton, request/response contract, ID plumbing, structured logging).
Everything else is a placeholder awaiting its own build-out.

## Getting started

Each folder will get its own README with setup instructions once it's
built. For now, `backend/README.md` is the only one with real content —
start there.
