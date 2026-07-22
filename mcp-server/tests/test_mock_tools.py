"""Tests for the seven MCP mock tools, run against a seeded Postgres.

These drive the *instrumented* handlers (``app.tools.TOOLS``), so they exercise
the real logging + fail_next + error-mapping wrapper, not just the bare
business functions.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from app.logging_utils import _JsonFormatter, get_logger
from app.tools import TOOLS
from app.tools.errors import RETRYABLE_MARKER, RetryableToolError, ToolError


# --- run_query ---------------------------------------------------------------


def test_run_query_returns_expected_failed_orders(run, make_ctx):
    ctx = make_ctx()
    result = run(
        TOOLS["run_query"](
            sql=(
                "SELECT id FROM orders "
                "WHERE status = 'failed' AND created_at >= now() - interval '7 days' "
                "ORDER BY id"
            ),
            ctx=ctx,
        )
    )
    assert result["row_count"] == 3
    assert [row["id"] for row in result["rows"]] == [1001, 1002, 1003]
    assert result["exec_ms"] >= 0


def test_run_query_caps_max_rows(run, make_ctx):
    result = run(TOOLS["run_query"](sql="SELECT id FROM orders ORDER BY id", max_rows=2, ctx=make_ctx()))
    assert result["row_count"] == 2


@pytest.mark.parametrize(
    "write_sql",
    [
        "INSERT INTO customers (id, name, region, created_at) VALUES (999, 'x', 'y', now())",
        "UPDATE orders SET status = 'failed' WHERE id = 1004",
        "DELETE FROM shipments WHERE id = 2001",
        "DROP TABLE documents",
    ],
)
def test_run_query_rejects_writes_as_toolerror(run, make_ctx, write_sql):
    with pytest.raises(ToolError) as excinfo:
        run(TOOLS["run_query"](sql=write_sql, ctx=make_ctx()))
    # A write rejection is permanent, never retryable.
    assert not isinstance(excinfo.value, RetryableToolError)
    assert RETRYABLE_MARKER not in str(excinfo.value)


# --- search_documents --------------------------------------------------------


def test_search_documents_returns_stable_escalation_ids(run, make_ctx):
    result = run(
        TOOLS["search_documents"](query="what is our escalation policy for failed orders", top_k=3, ctx=make_ctx())
    )
    assert result["count"] >= 1
    # The escalation policy doc must rank first, with a stable opaque chunk ID.
    assert result["retrieval_ids"][0] == "doc_007#chunk_1"
    assert all(rid.startswith("doc_007#chunk_") for rid in result["retrieval_ids"])
    # retrieval_ids mirror the result ids in order.
    assert result["retrieval_ids"] == [r["id"] for r in result["results"]]


# --- list_endpoints / http_get / http_post ----------------------------------


def test_list_endpoints_returns_allowlist(run, make_ctx):
    result = run(TOOLS["list_endpoints"](ctx=make_ctx()))
    names = {e["name"] for e in result["endpoints"]}
    assert {"get_shipment", "list_shipments"} <= names
    assert result["count"] == len(result["endpoints"])


def test_http_get_serves_seeded_shipment(run, make_ctx):
    result = run(TOOLS["http_get"](endpoint="get_shipment", params={"order_id": 1001}, ctx=make_ctx()))
    assert result["status_code"] == 200
    assert result["body"]["carrier"] == "UPS"
    assert result["body"]["status"] == "exception"
    assert result["latency_ms"] >= 0


def test_http_get_rejects_off_allowlist_endpoint(run, make_ctx):
    with pytest.raises(ToolError) as excinfo:
        run(TOOLS["http_get"](endpoint="drop_everything", params={}, ctx=make_ctx()))
    assert not isinstance(excinfo.value, RetryableToolError)


def test_http_post_failure_is_toolerror_not_retryable(run, make_ctx):
    # flaky_notify always returns 5xx; for a non-idempotent POST that is a
    # permanent ToolError, never a RetryableToolError.
    with pytest.raises(ToolError) as excinfo:
        run(TOOLS["http_post"](endpoint="flaky_notify", body={"message": "hi"}, ctx=make_ctx()))
    assert not isinstance(excinfo.value, RetryableToolError)
    assert RETRYABLE_MARKER not in str(excinfo.value)


def test_http_post_rejects_off_allowlist_endpoint(run, make_ctx):
    with pytest.raises(ToolError):
        run(TOOLS["http_post"](endpoint="get_shipment", body={}, ctx=make_ctx()))  # GET name, not POST


# --- fail_next ---------------------------------------------------------------


def test_fail_next_arms_exactly_one_failure_then_succeeds(run, make_ctx):
    ctx = make_ctx()
    armed = run(TOOLS["fail_next"](tool="http_get", count=1, ctx=ctx))
    assert armed["armed"] == 1

    # First call fails transiently...
    with pytest.raises(RetryableToolError) as excinfo:
        run(TOOLS["http_get"](endpoint="get_shipment", params={"order_id": 1002}, ctx=ctx))
    assert RETRYABLE_MARKER in str(excinfo.value)

    # ...the very next call (the "retry") succeeds.
    result = run(TOOLS["http_get"](endpoint="get_shipment", params={"order_id": 1002}, ctx=ctx))
    assert result["status_code"] == 200


# --- get_schema --------------------------------------------------------------


def test_get_schema_returns_column_metadata(run, make_ctx):
    result = run(TOOLS["get_schema"](tables=["orders", "shipments"], ctx=make_ctx()))
    order_cols = {c["column"] for c in result["tables"]["orders"]}
    assert {"id", "customer_id", "status", "total_cents", "created_at"} <= order_cols
    assert "shipments" in result["tables"]


# --- logging: metadata-only --------------------------------------------------


def _capture_logs(coro_factory, run):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        run(coro_factory())
    finally:
        logger.removeHandler(handler)
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_logs_never_contain_raw_rows_or_document_text(run, make_ctx):
    ctx = make_ctx()

    lines = _capture_logs(
        lambda: TOOLS["run_query"](sql="SELECT name FROM customers ORDER BY id", ctx=ctx),
        run,
    )
    blob = json.dumps(lines)
    # A real seeded customer name must never appear in any log line.
    assert "Acme Robotics" not in blob
    # The raw SQL must never appear — only an args digest.
    assert "SELECT name FROM customers" not in blob
    events = {ln["event"] for ln in lines}
    assert {"mcp.request", "mcp.response"} <= events
    request_line = next(ln for ln in lines if ln["event"] == "mcp.request")
    assert "args_digest" in request_line
    assert "sql" not in request_line
    response_line = next(ln for ln in lines if ln["event"] == "mcp.response")
    assert response_line["row_count"] == 5
    # Correlation IDs present on every line, and never a trace_id.
    for ln in lines:
        for key in ("run_id", "request_id", "session_id", "tenant_id"):
            assert key in ln
        assert "trace_id" not in ln


def test_logs_never_contain_document_text_only_ids(run, make_ctx):
    ctx = make_ctx()
    lines = _capture_logs(
        lambda: TOOLS["search_documents"](query="escalation policy for failed orders", ctx=ctx),
        run,
    )
    blob = json.dumps(lines)
    # Distinctive phrase from the escalation doc's body must never be logged...
    assert "on-call operator" not in blob
    assert "escalate to the fulfillment lead" not in blob
    # ...but the opaque retrieval IDs are logged (they're safe provenance).
    response_line = next(ln for ln in lines if ln["event"] == "mcp.response")
    assert "doc_007#chunk_1" in response_line["retrieval_ids"]
