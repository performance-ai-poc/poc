"""Coverage for the MCP client transport (app/mcp_client.py).

Live transport: the JSON-RPC handshake, the correlation IDs riding in the
tools/call params' ``_meta`` (the only channel the server reads them from —
mcp-server/app/logging_utils.py::ids_from_ctx), response unwrapping, and the
error contract (isError / protocol / transport failures, with RETRYABLE_MARKER
preserved). Driven against an in-process httpx.MockTransport, so no server runs.

Offline transport: the deterministic stand-ins mirror the live tools' shapes
(spec section 4.6) and error discipline (permanent vs transient), so an offline
run and a live run are indistinguishable downstream.

No pytest-asyncio in the suite, so coroutines are driven via asyncio.run (the
same convention as test_retry_helper.py).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import mcp_client
from app.agents.db.validation import (
    validate_document_result,
    validate_query_result,
    validate_schema_result,
)
from app.context import RequestContext
from app.mcp_client import (
    _OFFLINE_SHIPMENTS,
    McpToolError,
    _call_tool_live,
    _call_tool_offline,
    _correlation_meta,
    _result_from_call_tool,
    _unwrap_structured,
)
from app.retry import RETRYABLE_MARKER

CTX = RequestContext("run-abc", "req-def", "sess-ghi", "tenant-jkl")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_offline_fail_state():
    mcp_client._OFFLINE_FAIL_STATE.clear()
    yield
    mcp_client._OFFLINE_FAIL_STATE.clear()


# ---------------------------------------------------------------------------
# Live transport
# ---------------------------------------------------------------------------


@pytest.fixture
def drive(monkeypatch):
    """Drive ``_call_tool_live`` against a MockTransport whose tools/call step is
    answered by ``respond(body) -> httpx.Response``. Returns (result, captured),
    where captured holds the tools/call request body + headers. Propagates
    McpToolError so failure-path tests can wrap it in ``pytest.raises``."""

    def _drive(respond, *, ctx=CTX, session_id="sess-1", tool="list_endpoints", args=None):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            method = body.get("method")
            if method == "initialize":
                headers = {"mcp-session-id": session_id} if session_id else {}
                return httpx.Response(200, headers=headers, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
            if method == "notifications/initialized":
                return httpx.Response(202)
            if method == "tools/call":
                captured["body"] = body
                captured["headers"] = dict(request.headers)
                return respond(body)
            raise AssertionError(f"unexpected method {method!r}")

        transport = httpx.MockTransport(handler)
        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": transport}))
        result = _run(_call_tool_live(tool, args or {}, ctx=ctx))
        return result, captured

    return _drive


def _ok(result_dict):
    return lambda body: httpx.Response(
        200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"structuredContent": result_dict}}
    )


def test_live_completes_handshake_and_returns_tool_dict(drive):
    result, captured = drive(_ok({"endpoints": [], "count": 0}))
    assert result == {"endpoints": [], "count": 0}
    assert captured["body"]["method"] == "tools/call"


def test_live_puts_correlation_ids_in_meta(drive):
    _, captured = drive(_ok({"count": 0}))
    assert captured["body"]["params"]["_meta"] == {
        "run_id": "run-abc",
        "request_id": "req-def",
        "session_id": "sess-ghi",
        "tenant_id": "tenant-jkl",
    }


def test_live_without_context_omits_meta(drive):
    _, captured = drive(_ok({"count": 0}), ctx=None)
    assert "_meta" not in captured["body"]["params"]


def test_live_echoes_session_id_on_tool_call(drive):
    _, captured = drive(_ok({"count": 0}), session_id="sess-xyz")
    assert captured["headers"]["mcp-session-id"] == "sess-xyz"


def test_live_passes_tool_name_and_args(drive):
    _, captured = drive(_ok({"status_code": 200}), tool="http_get", args={"endpoint": "get_shipment"})
    assert captured["body"]["params"]["name"] == "http_get"
    assert captured["body"]["params"]["arguments"] == {"endpoint": "get_shipment"}


def test_live_tool_error_preserves_message_with_marker(drive):
    def respond(body):
        result = {"isError": True, "content": [{"type": "text", "text": f"{RETRYABLE_MARKER} boom"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    with pytest.raises(McpToolError) as exc:
        drive(respond)
    assert RETRYABLE_MARKER in str(exc.value)


def test_live_permanent_tool_error_has_no_marker(drive):
    def respond(body):
        result = {"isError": True, "content": [{"type": "text", "text": "not an allow-listed endpoint"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    with pytest.raises(McpToolError) as exc:
        drive(respond)
    assert RETRYABLE_MARKER not in str(exc.value)


def test_live_jsonrpc_protocol_error_raises(drive):
    def respond(body):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "no"}})

    with pytest.raises(McpToolError) as exc:
        drive(respond)
    assert RETRYABLE_MARKER not in str(exc.value)  # protocol errors are permanent


def test_live_http_5xx_is_retryable_transport_error(drive):
    with pytest.raises(McpToolError) as exc:
        drive(lambda body: httpx.Response(503))
    assert RETRYABLE_MARKER in str(exc.value)


def test_live_connect_error_is_retryable_transport_error(drive):
    def respond(body):
        raise httpx.ConnectError("refused")

    with pytest.raises(McpToolError) as exc:
        drive(respond)
    assert RETRYABLE_MARKER in str(exc.value)


def test_live_parses_sse_framed_response(drive):
    def respond(body):
        payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"structuredContent": {"count": 3}}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=f"data: {payload}\n")

    result, _ = drive(respond)
    assert result == {"count": 3}


# ---------------------------------------------------------------------------
# Live-transport helpers (edge cases, no transport)
# ---------------------------------------------------------------------------


def test_correlation_meta_keys_and_none():
    assert set(_correlation_meta(CTX)) == {"run_id", "request_id", "session_id", "tenant_id"}
    assert _correlation_meta(None) == {}


def test_unwrap_structured_passes_dict_through():
    assert _unwrap_structured({"rows": [], "row_count": 0}) == {"rows": [], "row_count": 0}


def test_unwrap_structured_unwraps_scalar_result():
    assert _unwrap_structured({"result": 7}) == {"result": 7}


def test_unwrap_structured_unwraps_dict_result():
    assert _unwrap_structured({"result": {"a": 1}}) == {"a": 1}


def test_unwrap_structured_wraps_non_dict():
    assert _unwrap_structured([1, 2]) == {"result": [1, 2]}


def test_result_from_call_tool_text_json_fallback():
    message = {"result": {"content": [{"type": "text", "text": json.dumps({"count": 2})}]}}
    assert _result_from_call_tool(message) == {"count": 2}


def test_result_from_call_tool_text_non_json_fallback():
    message = {"result": {"content": [{"type": "text", "text": "hello"}]}}
    assert _result_from_call_tool(message) == {"result": "hello"}


def test_result_from_call_tool_empty_result():
    assert _result_from_call_tool({"result": {}}) == {}


# ---------------------------------------------------------------------------
# Offline transport
# ---------------------------------------------------------------------------


def test_offline_list_endpoints_shape():
    result = _run(_call_tool_offline("list_endpoints", {}))
    assert set(result) == {"endpoints", "count"}
    assert result["count"] == len(result["endpoints"]) == 4


def test_offline_http_get_shipment_found():
    result = _run(_call_tool_offline("http_get", {"endpoint": "get_shipment", "params": {"order_id": 1001}}))
    assert set(result) == {"status_code", "body", "latency_ms"}
    assert result["status_code"] == 200
    assert result["body"]["order_id"] == 1001


def test_offline_http_get_shipment_missing_id_defaults_to_first():
    # Offline convenience: paramless get_shipment resolves to the first shipment.
    result = _run(_call_tool_offline("http_get", {"endpoint": "get_shipment", "params": {}}))
    assert result["status_code"] == 200
    assert result["body"]["order_id"] == _OFFLINE_SHIPMENTS[0]["order_id"]


def test_offline_http_get_shipment_unknown_is_404():
    result = _run(_call_tool_offline("http_get", {"endpoint": "get_shipment", "params": {"order_id": 9999}}))
    assert result["status_code"] == 404
    assert result["body"]["error"] == "shipment_not_found"


def test_offline_http_get_list_shipments_all():
    result = _run(_call_tool_offline("http_get", {"endpoint": "list_shipments", "params": {}}))
    assert result["status_code"] == 200
    assert result["body"]["count"] == 8


def test_offline_http_get_list_shipments_status_filter():
    result = _run(_call_tool_offline("http_get", {"endpoint": "list_shipments", "params": {"status": "in_transit"}}))
    statuses = {s["status"] for s in result["body"]["shipments"]}
    assert statuses == {"in_transit"}


def test_offline_http_get_off_allow_list_is_permanent():
    with pytest.raises(McpToolError) as exc:
        _run(_call_tool_offline("http_get", {"endpoint": "nope", "params": {}}))
    assert RETRYABLE_MARKER not in str(exc.value)


def test_offline_http_post_create_note():
    result = _run(_call_tool_offline("http_post", {"endpoint": "create_shipment_note", "body": {"order_id": 1001, "note": "hi"}}))
    assert result["status_code"] == 201
    assert result["body"] == {"created": True, "order_id": 1001, "note_len": 2}


def test_offline_http_post_flaky_notify_is_permanent():
    with pytest.raises(McpToolError) as exc:
        _run(_call_tool_offline("http_post", {"endpoint": "flaky_notify", "body": {}}))
    assert RETRYABLE_MARKER not in str(exc.value)


def test_offline_http_post_off_allow_list_is_permanent():
    with pytest.raises(McpToolError) as exc:
        _run(_call_tool_offline("http_post", {"endpoint": "get_shipment", "body": {}}))
    assert RETRYABLE_MARKER not in str(exc.value)


def test_offline_unknown_tool_raises():
    with pytest.raises(McpToolError):
        _run(_call_tool_offline("does_not_exist", {}))


def test_offline_fail_next_arms_one_transient_failure_then_recovers():
    _run(_call_tool_offline("fail_next", {"tool": "http_get", "count": 1}))
    with pytest.raises(McpToolError) as exc:
        _run(_call_tool_offline("http_get", {"endpoint": "list_shipments", "params": {}}))
    assert RETRYABLE_MARKER in str(exc.value)
    # Consumed — the next call succeeds.
    ok = _run(_call_tool_offline("http_get", {"endpoint": "list_shipments", "params": {}}))
    assert ok["status_code"] == 200


def test_offline_fail_next_can_arm_any_tool():
    _run(_call_tool_offline("fail_next", {"tool": "list_endpoints", "count": 1}))
    with pytest.raises(McpToolError):
        _run(_call_tool_offline("list_endpoints", {}))


# ---------------------------------------------------------------------------
# Offline DB tools — validated against the DB agent's own contracts, since the
# DB agent (app/agents/db) is the consumer of these three tools.
# ---------------------------------------------------------------------------


def test_offline_get_schema_matches_db_contract():
    result = _run(_call_tool_offline("get_schema", {"tables": ["orders", "shipments", "unknown"]}))
    schema = validate_schema_result(result)  # raises if the shape is wrong
    assert [c.column for c in schema.tables["orders"]][:3] == ["id", "customer_id", "status"]
    # An unknown table comes back with an empty column list, like the live tool.
    assert schema.tables["unknown"] == []


def test_offline_run_query_failed_orders_last_week():
    sql = "SELECT id, status FROM orders WHERE status = 'failed' AND created_at >= now() - interval '7 days'"
    result = _run(_call_tool_offline("run_query", {"sql": sql}))
    parsed = validate_query_result(result)
    assert parsed.row_count == 3
    assert [row["id"] for row in parsed.rows] == [1001, 1002, 1003]


def test_offline_run_query_selects_table_by_from_clause():
    customers = validate_query_result(_run(_call_tool_offline("run_query", {"sql": "SELECT * FROM customers"})))
    assert {row["name"] for row in customers.rows} == {"Acme Robotics", "Globex Logistics", "Initech Retail"}
    shipments = validate_query_result(_run(_call_tool_offline("run_query", {"sql": "SELECT * FROM shipments"})))
    assert all("tracking_number" in row for row in shipments.rows)


def test_offline_search_documents_matches_db_contract():
    result = _run(_call_tool_offline("search_documents", {"query": "escalation policy"}))
    parsed = validate_document_result(result)
    assert parsed.retrieval_ids == ["doc_007#chunk_1"]
    assert parsed.count == 1
