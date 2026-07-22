"""MCP server configuration.

Same pattern as ``orchestrator-svc/app/config.py``: every configurable value is
read from the environment (optionally via a ``.env`` file) through
pydantic-settings. Nothing operational is hardcoded — the two DSNs and the
timeout/latency knobs are the seams that compose / Helm / a future secret store
plug into without touching application code.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    # Read-write DSN — used ONLY by seeding (app/seed/build_seed.py). It is
    # never handed to a tool: data_access.py deliberately keeps the read-write
    # path (rw_connection) separate from the read-only path the tools use.
    database_url: str = "postgresql://app:app@localhost:5432/appdb"

    # Read-only DSN — used by run_query / search_documents / get_schema. It
    # should point at a role that only holds SELECT grants, so the SELECT-only
    # guarantee is enforced by the database (belt-and-suspenders with the
    # per-transaction READ ONLY + statement_timeout set in data_access.py).
    readonly_database_url: str = "postgresql://mcp_readonly:mcp_readonly@localhost:5432/appdb"

    # Ceiling on how long any single read-only query may run, in milliseconds.
    # Applied as SET LOCAL statement_timeout inside the read-only transaction; a
    # query that exceeds it is cancelled by Postgres and surfaced as a
    # RetryableToolError (a timeout is transient, hence retryable).
    statement_timeout_ms: int = 3000

    # Artificial per-tool latency (ms) injected by the instrumentation wrapper
    # before the handler runs. 0 = off. Lets the demo/dashboard show non-trivial
    # tool durations without changing tool code.
    mock_tool_latency_ms: int = 0

    # Stamped on every log line as service.name so records from this service can
    # be told apart from the orchestrator's ("backend-api"). See logging_utils.
    service_name: str = "mcp-server"


settings = Settings()
