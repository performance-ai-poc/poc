"""MCP tool transport for the sub-agents.

``call_tool(tool_name, args)`` is the single seam every tool call flows
through (wrapped by app/retry.py's retry helper). It has two modes, selected
by ``settings.agent_live_calls``:

  - Live (True): a real streamable-http call to the MCP server
    (mcp-server/app/server.py on :8000), spoken as JSON-RPC over HTTP with
    httpx. The server runs with json_response=True, so responses are plain
    JSON (no SSE framing) and no separate MCP SDK is needed — which also keeps
    this service free of the SDK's newer starlette pin that conflicts with the
    fastapi version it is built against. The four correlation IDs travel as
    request headers so the server's own logs stay joinable on run_id (spec
    section 2).
  - Offline (False, the default): deterministic in-process responses that
    mirror the MCP tools' shapes, so the agent — and its telemetry — runs
    end-to-end with no MCP server, no network, and no flakiness. This is what
    the test suite and any un-provisioned environment use.

Error contract (mcp-server/app/tools/errors.py): a failed tool *raises*, and
only its message string crosses the wire. A transient failure's message
contains :data:`~app.retry.RETRYABLE_MARKER`; everything else is permanent. A
tool that *returns* a dict (any status_code, incl. 404) succeeded. This module
preserves that signal: FastMCP ``isError`` responses and transport failures are
re-raised with the message intact so the retry helper can classify them, and
the offline stub raises the same shapes.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.context import RequestContext
from app.retry import RETRYABLE_MARKER

# JSON-RPC / MCP protocol constants for the minimal streamable-http client.
_PROTOCOL_VERSION = "2025-06-18"
# The streamable-http transport requires the client to accept both content
# types even when the server replies with JSON (json_response=True).
_ACCEPT = "application/json, text/event-stream"


class McpToolError(Exception):
    """A tool call failed. The message is the server's error text (which carries
    RETRYABLE_MARKER for transient failures), so the retry helper can classify
    it. Never constructed with raw request data — only the tool's own error
    string or a short transport category."""


async def call_tool(tool_name: str, args: dict, *, ctx: RequestContext | None = None) -> dict:
    """Call an MCP tool and return its structured result as a plain dict.

    Raises :class:`McpToolError` on failure (transport, or a tool that raised),
    with the message preserved so the retry helper can decide retryability.
    """
    if settings.agent_live_calls:
        return await _call_tool_live(tool_name, args, ctx=ctx)
    return await _call_tool_offline(tool_name, args)


# ---------------------------------------------------------------------------
# Live transport
# ---------------------------------------------------------------------------


def _correlation_headers(ctx: RequestContext | None) -> dict:
    if ctx is None:
        return {}
    return {
        "x-run-id": ctx.run_id,
        "x-request-id": ctx.request_id,
        "x-session-id": ctx.session_id,
        "x-tenant-id": ctx.tenant_id,
    }


def _unwrap_structured(payload: Any) -> dict:
    """Normalise FastMCP's structured output into the tool's own dict.

    FastMCP returns a tool's dict result as-is in ``structuredContent``, but
    wraps a non-object return under a single ``{"result": ...}`` key. Unwrap
    that so callers always see the tool's native shape.
    """
    if isinstance(payload, dict):
        if set(payload.keys()) == {"result"}:
            inner = payload["result"]
            return inner if isinstance(inner, dict) else {"result": inner}
        return payload
    return {"result": payload}


def _first_text(result: dict) -> str | None:
    for block in result.get("content", []) or []:
        text = block.get("text") if isinstance(block, dict) else None
        if text:
            return text
    return None


def _parse_jsonrpc_body(response: Any) -> dict:
    """Parse a streamable-http response body into the JSON-RPC message.

    With json_response=True the body is a plain JSON object. Defensively also
    handles an SSE-framed body (``data: {...}`` lines) in case the server is
    ever reconfigured, so this client keeps working either way.
    """
    text = response.text
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise ValueError("no data frame in SSE response")
    return response.json()


def _result_from_call_tool(message: dict) -> dict:
    """Extract a tool's dict result from a JSON-RPC tools/call response.

    Raises :class:`McpToolError` for a tool error (``isError``) — preserving the
    server's message text so RETRYABLE_MARKER survives — or a JSON-RPC protocol
    error. A successful call returns the tool's structured dict.
    """
    if "error" in message:
        # JSON-RPC protocol-level error (malformed request, bad method, …) —
        # permanent (no marker), surfaced immediately.
        raise McpToolError(f"mcp protocol error: {message['error']}")
    result = message.get("result", {})
    if result.get("isError"):
        raise McpToolError(_first_text(result) or "mcp tool error")
    if result.get("structuredContent") is not None:
        return _unwrap_structured(result["structuredContent"])
    text = _first_text(result)
    if text:
        try:
            return _unwrap_structured(json.loads(text))
        except json.JSONDecodeError:
            return {"result": text}
    return {}


async def _call_tool_live(tool_name: str, args: dict, *, ctx: RequestContext | None) -> dict:
    import httpx

    base_headers = {
        "Content-Type": "application/json",
        "Accept": _ACCEPT,
        **_correlation_headers(ctx),
    }

    try:
        async with httpx.AsyncClient(timeout=settings.mcp_timeout_s) as http:
            # 1. initialize handshake — the server returns a session id header the
            #    subsequent calls must echo back.
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "orchestrator-svc", "version": "0.1.0"},
                },
            }
            init_response = await http.post(settings.mcp_server_url, json=init_request, headers=base_headers)
            init_response.raise_for_status()
            _parse_jsonrpc_body(init_response)  # validate; raises on a JSON-RPC error

            session_id = init_response.headers.get("mcp-session-id")
            session_headers = dict(base_headers)
            if session_id:
                session_headers["mcp-session-id"] = session_id

            # 2. notify initialized (no response body expected).
            await http.post(
                settings.mcp_server_url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=session_headers,
            )

            # 3. call the tool.
            call_response = await http.post(
                settings.mcp_server_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": args},
                },
                headers=session_headers,
            )
            call_response.raise_for_status()
    except httpx.HTTPError as exc:
        # Connection refused/reset, DNS, timeout, or a non-2xx HTTP status from
        # the MCP endpoint itself (not a tool error — those come back as a 200
        # with isError). Transport failures are transient: mark them retryable
        # so an idempotent call retries.
        raise McpToolError(f"{RETRYABLE_MARKER} mcp transport: {type(exc).__name__}") from exc

    return _result_from_call_tool(_parse_jsonrpc_body(call_response))


# ---------------------------------------------------------------------------
# Offline transport — deterministic stand-ins for the MCP tools the REST API
# Agent uses. Shapes mirror mcp-server/app/tools/ (name-keyed endpoints, the
# {status_code, body, latency_ms} http shape, and the raised RETRYABLE_MARKER
# for simulated failures) so nothing downstream can tell the two modes apart.
# ---------------------------------------------------------------------------

# Mirrors mcp-server/app/seed/endpoints.json — the allow-list catalog. Tools are
# addressed by "name" (not "path"), matching http_tools.py.
_OFFLINE_ENDPOINTS = [
    {"name": "get_shipment", "method": "GET", "path": "/shipments/by-order/{order_id}", "params": ["order_id"]},
    {"name": "list_shipments", "method": "GET", "path": "/shipments", "params": ["status"]},
    {"name": "create_shipment_note", "method": "POST", "path": "/shipments/{order_id}/notes", "params": ["order_id", "note"]},
    {"name": "flaky_notify", "method": "POST", "path": "/notify", "params": ["message"]},
]

_OFFLINE_SHIPMENTS = [
    {"order_id": 1001, "carrier": "UPS", "tracking_number": "1Z001", "status": "in_transit", "last_update": "2026-07-21"},
    {"order_id": 1002, "carrier": "FedEx", "tracking_number": "FX002", "status": "delivered", "last_update": "2026-07-20"},
    {"order_id": 1003, "carrier": "USPS", "tracking_number": "US003", "status": "exception", "last_update": "2026-07-19"},
]

_OFFLINE_ENDPOINT_BY_NAME = {ep["name"]: ep for ep in _OFFLINE_ENDPOINTS}

# fail_next arming, mirroring mcp-server/app/tools/control.py.
_OFFLINE_FAIL_STATE: dict[str, int] = {}


def _offline_maybe_fail(tool: str) -> None:
    remaining = _OFFLINE_FAIL_STATE.get(tool, 0)
    if remaining > 0:
        _OFFLINE_FAIL_STATE[tool] = remaining - 1
        raise McpToolError(f"{RETRYABLE_MARKER} simulated transient failure for tool '{tool}' (fail_next)")


async def _call_tool_offline(tool_name: str, args: dict) -> dict:
    if tool_name == "list_endpoints":
        return {"endpoints": _OFFLINE_ENDPOINTS, "count": len(_OFFLINE_ENDPOINTS)}

    if tool_name == "fail_next":
        tool = str(args.get("tool", ""))
        count = int(args.get("count", 1))
        _OFFLINE_FAIL_STATE[tool] = _OFFLINE_FAIL_STATE.get(tool, 0) + max(0, count)
        return {"armed": _OFFLINE_FAIL_STATE[tool], "tool": tool, "count": count}

    if tool_name == "http_get":
        _offline_maybe_fail("http_get")
        endpoint = str(args.get("endpoint", ""))
        params = args.get("params") or {}
        ep = _OFFLINE_ENDPOINT_BY_NAME.get(endpoint)
        if ep is None or ep["method"] != "GET":
            raise McpToolError(f"endpoint '{endpoint}' is not an allow-listed GET endpoint")
        if endpoint == "get_shipment":
            order_id = params.get("order_id", _OFFLINE_SHIPMENTS[0]["order_id"])
            shipment = next((s for s in _OFFLINE_SHIPMENTS if s["order_id"] == order_id), None)
            if shipment is None:
                return {"status_code": 404, "body": {"error": "shipment_not_found", "order_id": order_id}, "latency_ms": 8}
            return {"status_code": 200, "body": shipment, "latency_ms": 8}
        # list_shipments
        status = params.get("status")
        shipments = [s for s in _OFFLINE_SHIPMENTS if not status or s["status"] == status]
        return {"status_code": 200, "body": {"shipments": shipments, "count": len(shipments)}, "latency_ms": 9}

    if tool_name == "http_post":
        _offline_maybe_fail("http_post")
        endpoint = str(args.get("endpoint", ""))
        body = args.get("body") or {}
        ep = _OFFLINE_ENDPOINT_BY_NAME.get(endpoint)
        if ep is None or ep["method"] != "POST":
            raise McpToolError(f"endpoint '{endpoint}' is not an allow-listed POST endpoint")
        if endpoint == "flaky_notify":
            # Mirrors the server: always fails, and POST failures are permanent
            # (non-idempotent) — no marker, so the retry helper never retries it.
            raise McpToolError("http_post to 'flaky_notify' returned 503 (non-idempotent, not retried)")
        # create_shipment_note
        return {
            "status_code": 201,
            "body": {"created": True, "order_id": body.get("order_id"), "note_len": len(str(body.get("note", "")))},
            "latency_ms": 11,
        }

    raise McpToolError(f"unknown tool '{tool_name}'")
