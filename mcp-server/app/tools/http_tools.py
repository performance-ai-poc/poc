"""HTTP-style MCP tools: ``list_endpoints``, ``http_get``, ``http_post``.

An in-process mock "shipping API" serves an **allow-listed** catalog
(``app/seed/endpoints.json``) out of the seeded ``shipments`` table via the
read-only DB path. No real network call is made — the tool *is* the mock server.

Idempotency drives the error taxonomy:
- ``http_get`` is idempotent -> transient failures are ``RetryableToolError``
  (and the ``fail_next`` injector arms this one for the demo's retry path).
- ``http_post`` is non-idempotent -> *any* failure (off-allow-list, or a
  non-2xx from the mock) is a ``ToolError`` and must never be retried.

Requests to endpoints not in the allow-list are rejected as ``ToolError``
(validation / 4xx), never served.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context

from app.tools import data_access
from app.tools.errors import ToolError

_ENDPOINTS_PATH = Path(__file__).resolve().parent.parent / "seed" / "endpoints.json"
_endpoints_cache: list[dict] | None = None


def _load_endpoints() -> list[dict]:
    global _endpoints_cache
    if _endpoints_cache is None:
        _endpoints_cache = json.loads(_ENDPOINTS_PATH.read_text())["endpoints"]
    return _endpoints_cache


def _endpoint(name: str) -> dict | None:
    return next((e for e in _load_endpoints() if e["name"] == name), None)


async def list_endpoints(ctx: Context = None) -> dict:
    """Return the allow-list catalog of mock shipping-API endpoints.

    Returns:
        ``{"endpoints": [{"name", "method", "path", "params"}, ...], "count": <int>}``.
        Only endpoints in this catalog can be called by ``http_get``/``http_post``.
    """
    endpoints = _load_endpoints()
    return {"endpoints": endpoints, "count": len(endpoints)}


async def http_get(endpoint: str, params: dict | None = None, ctx: Context = None) -> dict:
    """Call an allow-listed GET endpoint on the mock shipping API (idempotent).

    Args:
        endpoint: Allow-listed endpoint name (see ``list_endpoints``), e.g. ``"get_shipment"``.
        params: Path/query parameters, e.g. ``{"order_id": 1001}``.

    Returns:
        ``{"status_code": <int>, "body": <dict>, "latency_ms": <float>}``. A valid
        call that finds nothing returns ``status_code`` 404 as data (not an error).
    """
    params = params or {}
    ep = _endpoint(endpoint)
    if ep is None or ep["method"] != "GET":
        raise ToolError(f"endpoint '{endpoint}' is not an allow-listed GET endpoint")
    start = time.perf_counter()
    status_code, body = await asyncio.to_thread(_serve_get, ep, params)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return {"status_code": status_code, "body": body, "latency_ms": latency_ms}


async def http_post(endpoint: str, body: dict | None = None, ctx: Context = None) -> dict:
    """Call an allow-listed POST endpoint on the mock shipping API (non-idempotent).

    Because POST is non-idempotent, *every* failure here is a permanent
    ``ToolError`` — off-allow-list, and even a simulated 5xx from the mock — so
    the client's retry helper never re-sends it.

    Args:
        endpoint: Allow-listed POST endpoint name (see ``list_endpoints``).
        body: Request body.

    Returns:
        ``{"status_code": <int>, "body": <dict>, "latency_ms": <float>}`` on 2xx.
    """
    body = body or {}
    ep = _endpoint(endpoint)
    if ep is None or ep["method"] != "POST":
        raise ToolError(f"endpoint '{endpoint}' is not an allow-listed POST endpoint")
    start = time.perf_counter()
    try:
        status_code, resp = await asyncio.to_thread(_serve_post, ep, body)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 — non-idempotent: never retryable.
        raise ToolError(f"http_post to '{endpoint}' failed: {type(exc).__name__}")
    if status_code >= 400:
        raise ToolError(
            f"http_post to '{endpoint}' returned {status_code} (non-idempotent, not retried)"
        )
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return {"status_code": status_code, "body": resp, "latency_ms": latency_ms}


# --- in-process mock handlers (run in a worker thread) ----------------------


def _serve_get(ep: dict, params: dict) -> tuple[int, Any]:
    name = ep["name"]
    if name == "get_shipment":
        order_id = params.get("order_id")
        if order_id is None:
            return 400, {"error": "order_id is required"}
        result = data_access.execute_readonly(
            "SELECT order_id, carrier, tracking_number, status, last_update "
            "FROM shipments WHERE order_id = %s ORDER BY id LIMIT 1",
            (order_id,),
            max_rows=1,
        )
        if not result["rows"]:
            return 404, {"error": "shipment_not_found", "order_id": order_id}
        return 200, result["rows"][0]

    if name == "list_shipments":
        status = params.get("status")
        if status:
            result = data_access.execute_readonly(
                "SELECT order_id, carrier, tracking_number, status, last_update "
                "FROM shipments WHERE status = %s ORDER BY order_id",
                (status,),
                max_rows=50,
            )
        else:
            result = data_access.execute_readonly(
                "SELECT order_id, carrier, tracking_number, status, last_update "
                "FROM shipments ORDER BY order_id",
                None,
                max_rows=50,
            )
        return 200, {"shipments": result["rows"], "count": result["row_count"]}

    return 404, {"error": "unhandled_endpoint", "name": name}


def _serve_post(ep: dict, body: dict) -> tuple[int, Any]:
    name = ep["name"]
    if name == "create_shipment_note":
        order_id = body.get("order_id")
        note = body.get("note", "")
        if order_id is None:
            return 400, {"error": "order_id is required"}
        # Deterministic, in-memory acknowledgement (no persistence — POST is a
        # non-idempotent demo affordance, not a real write path).
        return 201, {"created": True, "order_id": order_id, "note_len": len(str(note))}

    if name == "flaky_notify":
        # Always "fails" with a 5xx so http_post's non-retryable path is
        # demonstrable: even a server error must not be retried for a POST.
        return 503, {"error": "carrier_unavailable"}

    return 404, {"error": "unhandled_endpoint", "name": name}
