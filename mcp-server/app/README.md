# MCP Server App

Python package containing the FastMCP server implementation.

- `server.py` — creates the `FastMCP` instance, registers the demo `add` tool
  and the seven mock tools (`register(mcp)`), and starts the server with
  `streamable-http`.
- `config.py` — pydantic-settings (DSNs + timeout/latency knobs).
- `logging_utils.py` — server-side correlated JSON logging (`mcp.request` /
  `mcp.response` / `mcp.error`), metadata-only, fail-open.
- `tools/` — the tool layer:
  - `__init__.py` — `register(mcp)` + the instrumentation wrapper (correlation
    IDs, logging, latency, `fail_next` hook). Exposes `TOOLS` for tests.
  - `db_tools.py` — `get_schema`, `run_query`, `search_documents`.
  - `http_tools.py` — `list_endpoints`, `http_get`, `http_post` (+ in-process
    mock shipping API).
  - `control.py` — `fail_next`, `FAIL_STATE`, `maybe_fail`.
  - `errors.py` — `ToolError` (re-exported) and `RetryableToolError`.
  - `data_access.py` — psycopg connection sources: a pooled read-only path for
    the tools and a read-write path used only by seeding.
- `seed/` — `schema.sql`, `seed_data.py`, `endpoints.json`, `build_seed.py`.

See the service [README](../README.md) for tool contracts, the error/retry
model, the logging contract, and how to run compose / seed / demo / tests.
