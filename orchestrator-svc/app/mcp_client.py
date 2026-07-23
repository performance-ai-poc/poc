"""MCP tool transport for the sub-agents.

``call_tool(tool_name, args)`` is the single seam every tool call flows through
(wrapped by app/retry.py). Two modes, selected by ``settings.agent_live_calls``:

  - Live: JSON-RPC over streamable-http to the MCP server, spoken with httpx.
    The server runs json_response=True, so responses are plain JSON and no MCP
    SDK is needed.
  - Offline (default): deterministic in-process responses that mirror the tools
    the sub-agents call, so the agents and their telemetry run with no server or
    network. Used by the test suite and any un-provisioned environment.

Error contract (mcp-server/app/tools/errors.py): a failed tool raises, and only
its message string crosses the wire; a transient failure's message contains
:data:`RETRYABLE_MARKER`. A tool that returns a dict (any status_code, incl.
404) succeeded. This module preserves that signal so the retry helper can
classify failures.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.context import RequestContext
from app.retry import RETRYABLE_MARKER

_PROTOCOL_VERSION = "2025-06-18"
# streamable-http requires the client to accept both content types even when the
# server replies with JSON.
_ACCEPT = "application/json, text/event-stream"


class McpToolError(Exception):
    """A tool call failed. The message is the server's error text (which carries
    RETRYABLE_MARKER for transient failures) or a short transport category —
    never raw request data."""


async def call_tool(tool_name: str, args: dict, *, ctx: RequestContext | None = None) -> dict:
    """Call an MCP tool and return its structured result as a plain dict.

    Raises :class:`McpToolError` on failure, message preserved so the retry
    helper can decide retryability.
    """
    if settings.agent_live_calls:
        return await _call_tool_live(tool_name, args, ctx=ctx)
    return await _call_tool_offline(tool_name, args)


# ---------------------------------------------------------------------------
# Live transport
# ---------------------------------------------------------------------------


def _correlation_meta(ctx: RequestContext | None) -> dict:
    """The four IDs (plus W3C trace context) as request ``_meta`` — the only
    channel the server reads them from (``ctx.request_context.meta``;
    ``RequestParams.Meta`` allows extra keys). HTTP headers do not work: the
    server never inspects them.

    ``inject`` adds ``traceparent`` (and ``tracestate``/``baggage`` if present)
    for the currently-active span — here, the ``execute_tool`` span in
    app/retry.py — so the MCP server can parent its own ``mcp.tool`` span on
    ours, producing one distributed trace across the process boundary
    (docs/SEMCONV.md §6). This reuses the existing ``_meta`` channel; no new
    transport. It is a no-op when no span is active (offline mode / OTel off),
    and can never break a tool call."""
    meta = dict(ctx.as_dict()) if ctx is not None else {}
    try:
        from opentelemetry.propagate import inject

        inject(meta)
    except Exception:  # noqa: BLE001 — telemetry must never break a tool call.
        pass
    return meta


def _unwrap_structured(payload: Any) -> dict:
    """Return a tool's own dict. FastMCP passes a dict result through unchanged
    but wraps a non-object return under ``{"result": ...}``; undo that."""
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
    """Parse a streamable-http body into its JSON-RPC message. Handles the plain
    JSON body (json_response=True) and, defensively, an SSE-framed one."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise ValueError("no data frame in SSE response")
    return response.json()


def _result_from_call_tool(message: dict) -> dict:
    """Extract a tool's dict result from a tools/call response, raising
    :class:`McpToolError` for a tool error (``isError``, message preserved so
    RETRYABLE_MARKER survives) or a JSON-RPC protocol error."""
    if "error" in message:
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

    headers = {"Content-Type": "application/json", "Accept": _ACCEPT}

    try:
        async with httpx.AsyncClient(timeout=settings.mcp_timeout_s) as http:
            # 1. initialize; the server returns a session id header to echo back.
            init_response = await http.post(
                settings.mcp_server_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "orchestrator-svc", "version": "0.1.0"},
                    },
                },
                headers=headers,
            )
            init_response.raise_for_status()

            session_id = init_response.headers.get("mcp-session-id")
            if session_id:
                headers["mcp-session-id"] = session_id

            # 2. notify initialized (no response body expected).
            await http.post(
                settings.mcp_server_url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=headers,
            )

            # 3. call the tool. The correlation IDs ride in params._meta so the
            #    server's logs join ours on run_id; omitted when there's no ctx.
            params: dict = {"name": tool_name, "arguments": args}
            meta = _correlation_meta(ctx)
            if meta:
                params["_meta"] = meta

            call_response = await http.post(
                settings.mcp_server_url,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params},
                headers=headers,
            )
            call_response.raise_for_status()
    except httpx.HTTPError as exc:
        # Connection/timeout, or a non-2xx from the endpoint itself (tool errors
        # come back as a 200 with isError). Transport failures are transient.
        raise McpToolError(f"{RETRYABLE_MARKER} mcp transport: {type(exc).__name__}") from exc

    return _result_from_call_tool(_parse_jsonrpc_body(call_response))


# ---------------------------------------------------------------------------
# Offline transport — deterministic stand-ins for the tools the REST API Agent
# calls (list_endpoints, http_get, http_post) plus the fail_next control tool.
# Shapes and error discipline mirror mcp-server/app/tools/ so nothing downstream
# can tell the two modes apart.
# ---------------------------------------------------------------------------

# Mirrors mcp-server/app/seed/endpoints.json — the allow-list catalog.
_OFFLINE_ENDPOINTS = [
    {"name": "get_shipment", "method": "GET", "path": "/shipments/by-order/{order_id}", "params": ["order_id"]},
    {"name": "list_shipments", "method": "GET", "path": "/shipments", "params": ["status"]},
    {"name": "create_shipment_note", "method": "POST", "path": "/shipments/{order_id}/notes", "params": ["order_id", "note"]},
    {"name": "flaky_notify", "method": "POST", "path": "/notify", "params": ["message"]},
]

# Mirrors mcp-server/app/seed/seed_data.py::SHIPMENTS.
_OFFLINE_SHIPMENTS = [
    {"order_id": 1001, "carrier": "UPS", "tracking_number": "1Z-ACME-0001", "status": "exception", "last_update": "2026-07-21"},
    {"order_id": 1002, "carrier": "FedEx", "tracking_number": "FX-GLOBEX-0002", "status": "lost", "last_update": "2026-07-19"},
    {"order_id": 1003, "carrier": "DHL", "tracking_number": "DHL-INITECH-0003", "status": "delayed", "last_update": "2026-07-17"},
    {"order_id": 1004, "carrier": "UPS", "tracking_number": "1Z-ACME-0004", "status": "delivered", "last_update": "2026-07-11"},
    {"order_id": 1005, "carrier": "FedEx", "tracking_number": "FX-GLOBEX-0005", "status": "in_transit", "last_update": "2026-07-15"},
    {"order_id": 1008, "carrier": "DHL", "tracking_number": "DHL-INITECH-0008", "status": "in_transit", "last_update": "2026-07-09"},
    {"order_id": 1009, "carrier": "UPS", "tracking_number": "1Z-UMBRELLA-0009", "status": "exception", "last_update": "2026-06-24"},
    {"order_id": 1011, "carrier": "FedEx", "tracking_number": "FX-ACME-0011", "status": "in_transit", "last_update": "2026-07-16"},
]

_OFFLINE_ENDPOINT_BY_NAME = {ep["name"]: ep for ep in _OFFLINE_ENDPOINTS}

# fail_next arming, mirroring mcp-server/app/tools/control.py.
_OFFLINE_FAIL_STATE: dict[str, int] = {}


def _offline_maybe_fail(tool: str) -> None:
    if _OFFLINE_FAIL_STATE.get(tool, 0) > 0:
        _OFFLINE_FAIL_STATE[tool] -= 1
        raise McpToolError(f"{RETRYABLE_MARKER} simulated transient failure for tool '{tool}' (fail_next)")


def _offline_http_get(args: dict) -> dict:
    endpoint = str(args.get("endpoint", ""))
    params = args.get("params") or {}
    ep = _OFFLINE_ENDPOINT_BY_NAME.get(endpoint)
    if ep is None or ep["method"] != "GET":
        raise McpToolError(f"endpoint '{endpoint}' is not an allow-listed GET endpoint")

    if endpoint == "get_shipment":
        # Offline convenience: a paramless call (as the default plan produces)
        # resolves to the first seeded shipment rather than the live server's 400.
        order_id = params.get("order_id", _OFFLINE_SHIPMENTS[0]["order_id"])
        shipment = next((s for s in _OFFLINE_SHIPMENTS if s["order_id"] == order_id), None)
        if shipment is None:
            return {"status_code": 404, "body": {"error": "shipment_not_found", "order_id": order_id}, "latency_ms": 8}
        return {"status_code": 200, "body": shipment, "latency_ms": 8}

    # list_shipments
    status = params.get("status")
    shipments = [s for s in _OFFLINE_SHIPMENTS if not status or s["status"] == status]
    return {"status_code": 200, "body": {"shipments": shipments, "count": len(shipments)}, "latency_ms": 9}


def _offline_http_post(args: dict) -> dict:
    endpoint = str(args.get("endpoint", ""))
    body = args.get("body") or {}
    ep = _OFFLINE_ENDPOINT_BY_NAME.get(endpoint)
    if ep is None or ep["method"] != "POST":
        raise McpToolError(f"endpoint '{endpoint}' is not an allow-listed POST endpoint")
    if endpoint == "flaky_notify":
        # Always fails; POST failures are permanent (no marker) — never retried.
        raise McpToolError("http_post to 'flaky_notify' returned 503 (non-idempotent, not retried)")
    # create_shipment_note
    return {
        "status_code": 201,
        "body": {"created": True, "order_id": body.get("order_id"), "note_len": len(str(body.get("note", "")))},
        "latency_ms": 11,
    }


async def _call_tool_offline(tool_name: str, args: dict) -> dict:
    if tool_name == "fail_next":
        tool = str(args.get("tool", ""))
        count = int(args.get("count", 1))
        _OFFLINE_FAIL_STATE[tool] = _OFFLINE_FAIL_STATE.get(tool, 0) + max(0, count)
        return {"armed": _OFFLINE_FAIL_STATE[tool], "tool": tool, "count": count}

    _offline_maybe_fail(tool_name)

    if tool_name == "list_endpoints":
        return {"endpoints": _OFFLINE_ENDPOINTS, "count": len(_OFFLINE_ENDPOINTS)}
    if tool_name == "http_get":
        return _offline_http_get(args)
    if tool_name == "http_post":
        return _offline_http_post(args)

    raise McpToolError(f"unknown tool '{tool_name}'")
