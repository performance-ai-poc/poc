"""Checks trace context round-trips across the MCP boundary: the orchestrator
injects W3C context into _meta and the MCP side rebuilds it, so the tool span
parents onto the caller's span. Exercises both real functions.

Run from orchestrator-svc/:
    ./.venv/Scripts/python.exe -m pytest ../otel/tests/test_trace_propagation.py -v
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import set_span_in_context

from app.context import RequestContext
from app.mcp_client import _correlation_meta

REPO_ROOT = Path(__file__).resolve().parents[2]

# Load the MCP server's telemetry module in isolation (by file path, under a
# unique module name) so its `app.telemetry` identity does not collide with the
# orchestrator's `app` package on sys.path. It imports only opentelemetry.*, so
# it loads cleanly under the orchestrator venv.
_spec = importlib.util.spec_from_file_location(
    "mcp_server_telemetry", REPO_ROOT / "mcp-server" / "app" / "telemetry.py"
)
_mcp_telemetry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mcp_telemetry)
extract_context = _mcp_telemetry.extract_context

# A real SDK tracer provider so spans have real (sampled) trace/span IDs;
# get_tracer against the default no-op provider would produce invalid contexts.
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer("test-trace-propagation")

CTX = RequestContext("run-1", "req-1", "sess-1", "tenant-1")

def _fake_server_ctx(meta_dict: dict):
    """Mimic a FastMCP Context: ctx.request_context.meta is a RequestParams.Meta
    (extra='allow'), i.e. an object with the _meta keys as attributes."""
    meta = SimpleNamespace(**meta_dict)
    return SimpleNamespace(request_context=SimpleNamespace(meta=meta))

def test_meta_carries_traceparent_for_the_active_span():
    with tracer.start_as_current_span("execute_tool") as client_span:
        meta = _correlation_meta(CTX)
        # correlation IDs still present…
        assert meta["run_id"] == "run-1"
        # …plus a W3C traceparent encoding THIS span's trace id.
        assert "traceparent" in meta, "no traceparent injected into _meta"
        trace_id_hex = format(client_span.get_span_context().trace_id, "032x")
        assert trace_id_hex in meta["traceparent"]

def test_server_extract_parents_the_tool_span_on_the_client_span():
    # Client side: produce _meta within an active span.
    with tracer.start_as_current_span("execute_tool") as client_span:
        meta = _correlation_meta(CTX)
    client_ctx = client_span.get_span_context()

    # Server side: rebuild context from _meta and start the tool span in it.
    parent_context = extract_context(_fake_server_ctx(meta))
    assert parent_context is not None, "extract_context found no trace context"

    tool_span = tracer.start_span("mcp.tool", context=parent_context)
    tool_ctx = tool_span.get_span_context()
    parent = tool_span.parent  # the SpanContext this span was created under
    tool_span.end()

    # Same trace, and the tool span's parent IS the client's execute_tool span.
    assert tool_ctx.trace_id == client_ctx.trace_id, "tool span is in a different trace"
    assert parent is not None and parent.span_id == client_ctx.span_id, (
        "mcp.tool did not parent onto the orchestrator's execute_tool span"
    )

def test_no_trace_context_yields_a_root_span_not_an_error():
    """When the client sent no trace context (offline mode / OTel off), the
    server must fall back to a new root span, never raise."""
    assert extract_context(_fake_server_ctx({"run_id": "run-1"})) is None
    assert extract_context(None) is None

def test_correlation_meta_never_raises_without_active_span():
    """Outside any span, _correlation_meta still returns the four IDs (and simply
    no traceparent) — telemetry must never break the tool-call path."""
    meta = _correlation_meta(CTX)
    assert meta["run_id"] == "run-1"
    # No active recording span -> inject writes an invalid/absent traceparent;
    # the call must not raise and the IDs must survive regardless.
    assert set(("run_id", "request_id", "session_id", "tenant_id")) <= set(meta)
