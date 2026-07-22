# MCP Server

FastMCP service for the Performance AI POC. It serves **seven mock tools** over
**streamable-http** on port `8000`, backed by **PostgreSQL 16** (native
full-text search), and emits its own **correlated JSON logs**.

This is the whole MCP **server** side of the POC. The MCP **client**
(orchestrator-side caller) and the shared retry helper live elsewhere; this
service is built to the two seams described under
[Coordination with the MCP client](#coordination-with-the-mcp-client).

## At a glance

- **Transport / framework:** FastMCP over `streamable-http`, port `8000`
  (instance named `mcp` in [`app/server.py`](app/server.py); the original demo
  `add` tool is still registered alongside the seven).
- **Storage:** PostgreSQL 16 with native full-text search
  (`tsvector`/`tsquery`, `ts_rank_cd`, GIN index). No vector stack.
- **SELECT-only, enforced by the database:** `run_query` runs as a read-only
  role inside a `READ ONLY` transaction with a `statement_timeout` — never by
  parsing SQL.
- **Errors:** `RetryableToolError` vs `ToolError`, with a wire-safe marker so
  the client can tell them apart.
- **Logging:** the server emits its own `mcp.request` / `mcp.response` /
  `mcp.error` lines — metadata-only, fail-open, `service.name="mcp-server"`.
- **Correlation IDs:** `run_id / request_id / session_id / tenant_id` (never a
  `trace_id`).

## Where it sits

```text
customer-ui → orchestrator-svc → MCP client ──(streamable-http + _meta)──▶ mcp-server ──▶ PostgreSQL 16
                                     ▲                                         │
                                     └─────────── correlated logs ────────────┘
                                        (both keyed on the same run_id)
```

## Project structure

```text
mcp-server/
  Dockerfile                 # installs psycopg, copies app+seed, streamable-http entrypoint
  requirements.txt
  app/
    server.py                # FastMCP instance + register(mcp)
    config.py                # pydantic-settings: DSNs, statement/latency knobs
    logging_utils.py         # correlated JSON logs (mcp.request/response/error), fail-open
    tools/
      __init__.py            # register(mcp): instrumentation wrapper (ctx + logging + errors); exposes TOOLS
      db_tools.py            # get_schema, run_query, search_documents
      http_tools.py          # list_endpoints, http_get, http_post (+ in-process mock shipping API)
      control.py             # fail_next + FAIL_STATE + maybe_fail
      errors.py              # ToolError (re-exported), RetryableToolError
      data_access.py         # psycopg pools: read-only (tools) + read-write (seed only)
    seed/
      schema.sql             # customers, orders, shipments, documents(tsv + GIN)
      seed_data.py           # deterministic rows + ~30 markdown docs, stable chunk IDs
      endpoints.json         # allow-list catalog for list_endpoints / http_*
      build_seed.py          # runs schema.sql + inserts + read-only role, idempotent
  scripts/
    demo_run.py              # streamable-http client that runs the demo scenario
  tests/
    conftest.py
    test_mock_tools.py
```

(A `docker-compose.yml` at the repo root wires Postgres 16 + this service.)

## Tools

All returns follow architecture §4.6. Each tool receives a FastMCP `Context`
(injected, and excluded from the input schema) that carries the four correlation
IDs used for logging.

| Tool | Args | Returns |
| --- | --- | --- |
| `get_schema` | `tables: list[str]` | `{"tables": {<table>: [{column, data_type, is_nullable, position}]}}` |
| `run_query` | `sql: str, max_rows=20` | `{"rows": [...], "row_count": int, "exec_ms": float}` |
| `search_documents` | `query: str, top_k=3` | `{"results": [{id, text, score}], "retrieval_ids": [...], "count": int}` |
| `list_endpoints` | – | `{"endpoints": [{name, method, path, params}], "count": int}` |
| `http_get` | `endpoint: str, params: dict` | `{"status_code": int, "body": dict, "latency_ms": float}` |
| `http_post` | `endpoint: str, body: dict` | `{"status_code": int, "body": dict, "latency_ms": float}` |
| `fail_next` | `tool: str, count=1` | `{"armed": int, "tool": str, "count": int}` |

Behavior notes:

- **`run_query` is SELECT-only, enforced by the database.** It executes as a
  read-only role inside a `READ ONLY` transaction with a `statement_timeout`; a
  write is rejected by Postgres (permission denied / read-only transaction) and
  surfaced as a non-retryable `ToolError`. A statement timeout becomes a
  `RetryableToolError`.
- **`search_documents`** uses `websearch_to_tsquery` + `ts_rank_cd` over a
  GIN-indexed generated `tsv` column. `id` is a **stable, opaque chunk ID**
  (e.g. `doc_007#chunk_1`); the flat `retrieval_ids` list is what the
  orchestrator threads through as provenance. `text` is returned to the caller
  but **never logged**.
- **`http_get` / `http_post`** are served by an in-process mock shipping API out
  of the seeded `shipments` table, restricted to the allow-list in
  [`app/seed/endpoints.json`](app/seed/endpoints.json). Off-allow-list calls are
  rejected as `ToolError`. `http_get` is idempotent (transient failures are
  retryable); `http_post` is non-idempotent (**every** failure is a permanent
  `ToolError`, never retried).

## Errors & retries

`mcp==1.28.1` ships only `ToolError`; `RetryableToolError` is defined here in
[`app/tools/errors.py`](app/tools/errors.py).

| Type | When | Retryable? |
| --- | --- | --- |
| `RetryableToolError` | transport / 5xx / timeout, and `fail_next` | yes |
| `ToolError` | validation / 4xx, `run_query` SELECT-guard, all `http_post` failures | no |

FastMCP collapses every tool exception into a `CallToolResult(isError=true, …)`
carrying only the message **string** — the exception class never crosses the
wire. So `RetryableToolError` embeds the marker **`[RETRYABLE]`** in its message,
and the retry contract with the client is: **an error result whose text contains
`[RETRYABLE]` is retryable; everything else is permanent.**

## `fail_next` and the latency knob

- `fail_next(tool, count)` arms `count` simulated transient failures for a tool
  (scenario 5). The next `count` calls raise `RetryableToolError` (simulated
  503); the client's retry then succeeds → `tool_selected → retried →
  tool_returned success`. This is a **tool-layer** injector, distinct from the
  orchestrator's `__FORCE_*_FAILURE__` graph-test triggers.
- `MOCK_TOOL_LATENCY_MS` (default `0`) injects artificial per-tool latency so
  the demo/dashboard can show non-trivial durations without code changes.

## Correlated logging

[`app/logging_utils.py`](app/logging_utils.py) mirrors the orchestrator's shape
and discipline: one JSON object per line to stdout, `service.name="mcp-server"`,
fail-open (`log_event` never raises), metadata-only.

- `mcp.request` on entry — `tool`, `args_digest` (never the raw args).
- `mcp.response` on success — `status`, `duration_ms`, plus whichever of
  `row_count` / `exec_ms` / `latency_ms` / `count` / `status_code` /
  `retrieval_ids` / `armed` apply.
- `mcp.error` on failure — `error_type`, `retryable`.

Every line carries `run_id / request_id / session_id / tenant_id` (never a
`trace_id`). Raw SQL rows, document text, and request/response bodies are never
logged.

Example (one armed `http_get` failing then succeeding on retry, same `run_id`):

```json
{"event": "mcp.request",  "tool": "http_get", "run_id": "demo-run-0001", "args_digest": "959a667430f2c429"}
{"event": "mcp.error",    "tool": "http_get", "run_id": "demo-run-0001", "retryable": true,  "error_type": "RetryableToolError"}
{"event": "mcp.request",  "tool": "http_get", "run_id": "demo-run-0001", "args_digest": "959a667430f2c429"}
{"event": "mcp.response", "tool": "http_get", "run_id": "demo-run-0001", "status": "success", "status_code": 200}
```

## Seed layout ([`app/seed/`](app/seed))

- `schema.sql` — `customers`, `orders`, `shipments` (FKs), and
  `documents(chunk_id PK, doc_id, text, tsv)` with a GIN index on `tsv`
  (generated column).
- `seed_data.py` — deterministic rows with fixed PKs. Three orders are `failed`
  within the last week (`1001/1002/1003`) and one is failed but older (`1009`),
  so a "failed orders from last week" filter returns exactly three. ~30 markdown
  docs are chunked into `documents` with stable IDs; `doc_007` is the escalation
  policy. (`orders.created_at` is seeded relative to now, so "last week" stays
  meaningful; the *set* of failed orders is fixed.)
- `endpoints.json` — the mock shipping API allow-list.
- `build_seed.py` — idempotent (drops + recreates every table), and also
  creates/grants the SELECT-only read-only role. No secrets or PII (customers
  are organizations; tracking numbers are synthetic).

## Configuration ([`app/config.py`](app/config.py))

pydantic-settings, read from env or `.env`:

| Env var | Purpose | Default |
| --- | --- | --- |
| `DATABASE_URL` | read-write DSN, used **only** by seeding | `postgresql://app:app@localhost:5432/appdb` |
| `READONLY_DATABASE_URL` | SELECT-only DSN used by the tools | `postgresql://mcp_readonly:mcp_readonly@localhost:5432/appdb` |
| `STATEMENT_TIMEOUT_MS` | per-query timeout | `3000` |
| `MOCK_TOOL_LATENCY_MS` | artificial per-tool latency | `0` |
| `LOG_LEVEL` | Python logging level | `INFO` |

## Running the stack (Docker Compose)

From the repo root:

```bash
docker compose up --build
```

Starts Postgres 16, seeds it deterministically (`build_seed.py`), then serves
all seven tools over streamable-http on `:8000`.

## Running locally (without Docker)

Against any reachable Postgres 16:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL="postgresql://<owner>@localhost:5432/appdb"
export READONLY_DATABASE_URL="postgresql://mcp_readonly:mcp_readonly@localhost:5432/appdb"

python -m app.seed.build_seed        # seed (idempotent; creates the read-only role)
python -m app.server                 # serve on :8000
```

## Demo & tests

Run the end-to-end scenario against a running server:

```bash
MCP_SERVER_URL=http://127.0.0.1:8000/mcp python scripts/demo_run.py
```

It runs *"Find all failed orders from last week, check their shipment status
with the carrier, and tell me what our escalation policy says"* — exercising
`run_query`, `http_get` with `fail_next` armed (→ retry → success), and
`search_documents` (→ `retrieval_ids`). One run touches every tool and the
failure path.

Unit tests (require a reachable Postgres via the env vars above; the suite seeds
it idempotently and skips cleanly if none is reachable):

```bash
pytest
```

## Coordination with the MCP client

The server is built to two seams — confirm these match the client:

1. **Tool names + arg/return shapes** — exactly the [Tools](#tools) table above.
2. **Correlation IDs + retry signal** — the client passes the four IDs in each
   call's request `_meta`
   (`ClientSession.call_tool(name, args, meta={run_id, request_id, session_id, tenant_id})`);
   the server reads them off `ctx.request_context.meta` (`RequestParams.Meta` is
   `extra="allow"`). Retryable errors are identified by the **`[RETRYABLE]`**
   marker in the error result's text.

Out of scope for this service: the MCP client, the shared retry helper, and the
orchestrator's agents.

## Container (single image)

```bash
docker build -t mcp-server:demo ./mcp-server   # or: make build-mcp
```
