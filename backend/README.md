# Performance AI — Backend

Minimal FastAPI skeleton for the Performance AI agent system. It defines the
request/response contract and request-identity plumbing that everything
else — a LangGraph orchestrator, the agents themselves, an MCP server, and an
OpenTelemetry instrumentation layer — will be built on top of. **There is no
real agent logic yet**: `/chat` returns a placeholder echo so the contract
can be exercised end-to-end before anything downstream exists.

## What's next

This is deliberately a skeleton, not a finished service. The intended next
steps are:

- Replace `app/service.py`'s `generate_reply` with a call into a LangGraph
  orchestrator that fans out to real agents.
- Add an MCP server as a tool/data source the orchestrator calls into.
- Point an OpenTelemetry layer at the structured JSON logs this service
  already emits (every log line carries `trace_id`, `request_id`,
  `session_id`, `tenant_id`) — no changes needed here for that to work.
- Add a redaction layer before any raw message content is persisted anywhere
  beyond the single response round-trip.

## Running locally

Requires Python 3.11+.

```powershell
cd backend
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8001
```

The server reads its port and log level from `.env` (see `.env.example`).
`.env` is git-ignored — copy the example rather than editing it in place.

## The contract

### `GET /health`

Always returns `200` and never depends on anything else in the process
(no DB, no telemetry backend). Used for standard container/load-balancer
health checks.

```json
{ "status": "ok" }
```

### `POST /chat`

Request body:

```json
{
  "message": "hello",
  "trace_id": "optional-caller-supplied-uuid",
  "request_id": "optional-caller-supplied-uuid",
  "session_id": "optional-caller-supplied-uuid",
  "tenant_id": "optional-caller-supplied-uuid"
}
```

Only `message` is required. Any of the four identifier fields may be
omitted; the server generates a UUID4 for whichever ones are missing.

Success response (`200`):

```json
{
  "reply": "Received: hello",
  "trace_id": "…",
  "request_id": "…",
  "session_id": "…",
  "tenant_id": "…"
}
```

Error response (`422` — missing/invalid body, `500` — unexpected failure):

```json
{
  "error": "invalid_request",
  "detail": "The request body is malformed or missing required fields (e.g. 'message').",
  "trace_id": "…",
  "request_id": "…",
  "session_id": "…",
  "tenant_id": "…"
}
```

The same four identifiers appear on every response — success or error —
because they are resolved by middleware before the request body is even
validated.

## Testing the endpoints

```bash
curl http://localhost:8001/health

curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'

# Supplying your own IDs (e.g. to resume a session):
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "session_id": "my-session-123"}'

# Triggering the validation error path (missing "message"):
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{}'
```

## How the four IDs flow through the code

1. `app/middleware.py`'s `RequestContextMiddleware` runs first for every
   request. It best-effort peeks the raw JSON body for `trace_id`,
   `request_id`, `session_id`, `tenant_id`, fills in anything missing via
   `app/context.py`'s `resolve_context` (UUID4 generation), and stores the
   resulting `RequestContext` on `request.state.context`. This happens
   *before* FastAPI validates the body against `ChatRequest`, so even a
   malformed request gets a fully-formed context.
2. Every route handler and exception handler pulls that context back out
   (`_context_from_request` in `app/main.py`) and passes it as an explicit
   argument to internal functions — see `generate_reply(message, ctx)` in
   `app/service.py`. Nothing relies on implicit thread-local/contextvar
   state for business logic; the context is a normal function argument.
3. `app/logging_utils.py`'s `log_event(ctx, event, ...)` requires a context
   as its first argument, so it's structurally impossible to emit a
   request-related log line without the four IDs attached. Every log line
   is one JSON object with `trace_id`, `request_id`, `session_id`,
   `tenant_id`, `endpoint`, `timestamp`, and (where applicable)
   `status_code`.
4. The same context is spread into every JSON response — success or
   error — via `**ctx.as_dict()`.

## Design notes

- **Fail-open logging**: `app/logging_utils.py` only ever writes to stdout.
  Nothing in the request path makes a synchronous call to an external
  telemetry/logging backend, so a down collector can never block or fail a
  request.
- **Metadata-only by default**: log lines never include the raw `message`
  body — only the four identifiers, endpoint, timestamp, and status. This
  keeps the skeleton safe to point at a shared log aggregator before a
  redaction layer exists.
- **No hardcoded config**: everything configurable (`PORT`, `LOG_LEVEL`,
  `APP_ENV`, and a placeholder `MODEL_API_KEY` for later) is read via
  `app/config.py` (`pydantic-settings`) from environment variables / `.env`.
