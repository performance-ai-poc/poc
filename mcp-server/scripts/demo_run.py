"""Streamable-HTTP MCP client that runs the demo scenario end to end.

Scenario:
    "Find all failed orders from last week, check their shipment status with the
     carrier, and tell me what our escalation policy says."

One run exercises every tool path and the failure path:
    run_query  -> failed orders from last week
    fail_next  -> arm one transient failure on http_get
    http_get   -> shipment status per order (first call fails -> retry -> success)
    search_documents -> escalation policy retrieval_ids

This is a deliberately minimal stand-in for the MCP-client teammate's caller. It
demonstrates the two seams we agreed on:

1. Correlation IDs: the four IDs (run_id/request_id/session_id/tenant_id) are
   passed in every call's request ``_meta`` (via ``call_tool(..., meta=...)``),
   so the server's mcp.request/mcp.response logs line up with the client's.
2. Retry signal: an error result whose text contains ``[RETRYABLE]`` is retried;
   everything else fails fast. (Server-side that marker is emitted by
   ``RetryableToolError``.) The real client owns a richer retry helper — this is
   just enough to show retry -> success.

Usage:
    MCP_SERVER_URL=http://127.0.0.1:8000/mcp python scripts/demo_run.py
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")

# Agreed seam with the server; see module docstring.
RETRYABLE_MARKER = "[RETRYABLE]"

# The four correlation IDs the orchestrator would mint for one end-to-end run.
CORRELATION_IDS = {
    "run_id": os.environ.get("RUN_ID", str(uuid.uuid4())),
    "request_id": str(uuid.uuid4()),
    "session_id": "demo-session",
    "tenant_id": "demo-tenant",
}


def _parse(result) -> dict:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    text = "".join(getattr(c, "text", "") for c in result.content)
    return json.loads(text) if text.strip() else {}


def _error_text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


async def call_tool_with_retry(session, name, arguments, *, max_attempts=3) -> dict:
    """Send a tool call; re-send on a [RETRYABLE] error, fail fast otherwise."""
    last = ""
    for attempt in range(1, max_attempts + 1):
        result = await session.call_tool(name, arguments, meta=CORRELATION_IDS)
        if not result.isError:
            return _parse(result)
        last = _error_text(result)
        if RETRYABLE_MARKER in last and attempt < max_attempts:
            print(f"   ↻ {name}: retryable failure on attempt {attempt} ({last.strip()}); retrying")
            continue
        raise RuntimeError(f"{name} failed permanently: {last}")
    raise RuntimeError(f"{name} exhausted retries: {last}")


async def main() -> None:
    print(f"Connecting to {SERVER_URL} …")
    async with streamablehttp_client(SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"run_id={CORRELATION_IDS['run_id']}\n")

            # 1) failed orders from last week
            query = (
                "SELECT id, customer_id, total_cents FROM orders "
                "WHERE status = 'failed' AND created_at >= now() - interval '7 days' "
                "ORDER BY id"
            )
            orders = await call_tool_with_retry(session, "run_query", {"sql": query, "max_rows": 20})
            failed = orders["rows"]
            print(
                f"1) run_query -> {orders['row_count']} failed orders last week "
                f"(exec_ms={orders['exec_ms']}): {[r['id'] for r in failed]}"
            )

            # 2) arm a single transient failure on http_get to force the retry path
            armed = await call_tool_with_retry(session, "fail_next", {"tool": "http_get", "count": 1})
            print(f"2) fail_next -> armed {armed['armed']} failure(s) on http_get")

            # 3) check carrier shipment status for each failed order (first call retries)
            print("3) http_get shipment status per failed order:")
            for row in failed:
                ship = await call_tool_with_retry(
                    session, "http_get", {"endpoint": "get_shipment", "params": {"order_id": row["id"]}}
                )
                body = ship["body"]
                print(
                    f"   order {row['id']}: {ship['status_code']} "
                    f"{body.get('carrier')} / {body.get('status')} (latency_ms={ship['latency_ms']})"
                )

            # 4) escalation policy
            docs = await call_tool_with_retry(
                session,
                "search_documents",
                {"query": "what is our escalation policy for failed orders", "top_k": 3},
            )
            print(f"4) search_documents -> retrieval_ids={docs['retrieval_ids']}")
            if docs["results"]:
                print(f"   policy excerpt: {docs['results'][0]['text'][:160]} …")

    print("\n✅ demo scenario complete — every tool and the retry path exercised.")


if __name__ == "__main__":
    asyncio.run(main())
