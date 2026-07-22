"""Shared pytest fixtures for the MCP server tool tests.

The suite runs against a *real* seeded Postgres (the tools' whole point is
database-enforced SELECT-only + native full-text search, which SQLite/mocks
can't stand in for). Point ``DATABASE_URL`` / ``READONLY_DATABASE_URL`` at a
Postgres 16 instance before running; the session fixture below seeds it
idempotently. If no database is reachable, the whole suite skips with a clear
message rather than failing noisily.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.context import RequestContext
from mcp.types import RequestParams

from app.tools import control, data_access

_FASTMCP = FastMCP("test-mcp-server")


@pytest.fixture(scope="session", autouse=True)
def _seeded_db():
    """Seed the database once per session; skip the suite if none is reachable."""
    from app.seed import build_seed

    try:
        build_seed.main()
    except Exception as exc:  # noqa: BLE001 — no DB -> skip, don't error.
        pytest.skip(f"no seeded Postgres reachable ({type(exc).__name__}: {exc})")
    yield
    data_access.close_ro_pool()


@pytest.fixture(autouse=True)
def _reset_control():
    """Isolate fail_next state between tests."""
    control.reset()
    yield
    control.reset()


@pytest.fixture
def make_ctx():
    """Factory for a FastMCP Context carrying the four correlation IDs in _meta."""

    def _make(**ids) -> Context:
        meta_dict = ids or {
            "run_id": "run-test",
            "request_id": "req-test",
            "session_id": "sess-test",
            "tenant_id": "tenant-test",
        }
        meta = RequestParams.Meta.model_validate(meta_dict)
        rc = RequestContext(
            request_id="mcp-req-1",
            meta=meta,
            session=object(),
            lifespan_context=None,
            request=None,
        )
        return Context(request_context=rc, fastmcp=_FASTMCP)

    return _make


@pytest.fixture
def run():
    """Run a coroutine to completion from a sync test."""

    def _run(coro):
        return asyncio.run(coro)

    return _run
