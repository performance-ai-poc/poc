"""Coverage for the shared retry helper — the single retry layer and the
source of the agent.tool_selected / agent.retried / agent.tool_returned
telemetry (architecture spec, sections 4.3 and 5.2).

Retry contract (mcp-server/app/tools/errors.py): a failed tool *raises*, and the
retryable-vs-permanent signal lives in the error text — transient failures carry
RETRYABLE_MARKER, everything else is permanent. A tool that *returns* a dict
(any status_code, incl. 404) succeeded. These tests drive the helper directly
with fake tool callables so the policy is exercised without a live MCP server,
and assert the log contract: which events fire, in what order, with which
metadata, and that raw args never leak (only a digest is logged).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

from app.context import resolve_context
from app.logging_utils import _JsonFormatter, get_logger
from app.retry import RETRYABLE_MARKER, ToolError, call_tool_with_retry


def _run_with_captured_logs(coro_factory):
    """Run an async helper call while capturing structured log lines.

    Returns (result_or_exception, log_lines). A raised exception is returned
    (not propagated) so tests can assert on both it and the emitted events.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        try:
            result = asyncio.run(coro_factory())
        except Exception as exc:  # noqa: BLE001 — returned for assertion.
            result = exc
    finally:
        logger.removeHandler(handler)

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    return result, lines


def _events(lines, name):
    return [line for line in lines if line["event"] == name]


def _call(tool_callable, *, tool_name="http_get", args=None, idempotent=True, backoff=(0.0, 0.0)):
    ctx = resolve_context()
    args = {"endpoint": "list_shipments", "params": {"status": "in_transit"}} if args is None else args

    def factory():
        return call_tool_with_retry(
            ctx,
            graph_node="api_agent",
            step_sequence=2,
            call_sequence=3,
            tool_name=tool_name,
            args=args,
            tool_callable=tool_callable,
            idempotent=idempotent,
            backoff=backoff,
        )

    return _run_with_captured_logs(factory)


def _retryable(message: str) -> RuntimeError:
    """A raised error that carries the retryable marker (what the MCP transport
    surfaces for a transient failure / armed fail_next)."""
    return RuntimeError(f"{RETRYABLE_MARKER} {message}")


# ---------------------------------------------------------------------------
# Success on the first attempt
# ---------------------------------------------------------------------------


def test_success_emits_selected_then_returned_no_retry():
    async def ok(_name, _args):
        return {"status_code": 200, "body": {"ok": True}, "latency_ms": 12}

    result, lines = _call(ok)

    assert result == {"status_code": 200, "body": {"ok": True}, "latency_ms": 12}
    assert len(_events(lines, "agent.tool_selected")) == 1
    assert len(_events(lines, "agent.retried")) == 0

    [returned] = _events(lines, "agent.tool_returned")
    assert returned["status"] == "success"
    assert returned["status_code"] == 200
    assert returned["graph.node"] == "api_agent"
    assert returned["step.sequence"] == 2
    assert returned["call.sequence"] == 3
    assert returned["tool_name"] == "http_get"
    assert "latency_ms" in returned


def test_every_emitted_line_carries_all_four_correlation_ids():
    async def ok(_name, _args):
        return {"status_code": 200, "body": {}, "latency_ms": 1}

    _, lines = _call(ok)
    assert lines
    for line in lines:
        for key in ("run_id", "request_id", "session_id", "tenant_id"):
            assert key in line


# ---------------------------------------------------------------------------
# A returned status_code — even 404 — is data (success), never an error
# ---------------------------------------------------------------------------


def test_returned_404_is_treated_as_success_not_error():
    async def not_found(_name, _args):
        return {"status_code": 404, "body": {"error": "shipment_not_found"}, "latency_ms": 5}

    result, lines = _call(not_found)

    assert result["status_code"] == 404  # returned to the caller as data
    [returned] = _events(lines, "agent.tool_returned")
    assert returned["status"] == "success"
    assert returned["status_code"] == 404
    assert _events(lines, "agent.retried") == []


# ---------------------------------------------------------------------------
# Retry-then-success on a marked (retryable) failure
# ---------------------------------------------------------------------------


def test_retryable_then_success_emits_one_retry_with_attempt_and_reason():
    calls = {"n": 0}

    async def flaky(_name, _args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _retryable("simulated transient failure (fail_next)")
        return {"status_code": 200, "body": {"ok": True}, "latency_ms": 7}

    result, lines = _call(flaky)

    assert result["status_code"] == 200
    # tool_selected before EACH attempt (2), one retried before the re-attempt.
    assert len(_events(lines, "agent.tool_selected")) == 2
    [retried] = _events(lines, "agent.retried")
    assert retried["retry.attempt"] == 1
    assert retried["reason"] == "retryable_tool_error"
    assert retried["graph.node"] == "api_agent"
    [returned] = _events(lines, "agent.tool_returned")
    assert returned["status"] == "success"
    assert returned["status_code"] == 200


# ---------------------------------------------------------------------------
# Exhaustion: 1 try + 2 retries, then terminal ToolError
# ---------------------------------------------------------------------------


def test_persistent_retryable_failure_exhausts_retries_then_raises_toolerror():
    async def always_fail(_name, _args):
        raise _retryable("still failing")

    result, lines = _call(always_fail)

    assert isinstance(result, ToolError)
    assert result.reason == "retryable_tool_error"
    # max_attempts=3 -> 3 selected, 2 retried (attempt 1 and 2), 1 error return.
    assert len(_events(lines, "agent.tool_selected")) == 3
    assert [r["retry.attempt"] for r in _events(lines, "agent.retried")] == [1, 2]
    [returned] = _events(lines, "agent.tool_returned")
    assert returned["status"] == "error"


# ---------------------------------------------------------------------------
# Timeouts are retryable and classified
# ---------------------------------------------------------------------------


def test_timeout_exception_is_classified_and_retried():
    calls = {"n": 0}

    async def timeout_once(_name, _args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("simulated")
        return {"status_code": 200, "body": {}, "latency_ms": 4}

    result, lines = _call(timeout_once)

    assert result["status_code"] == 200
    [retried] = _events(lines, "agent.retried")
    assert retried["reason"] == "timeout"


# ---------------------------------------------------------------------------
# Policy: permanent (unmarked) errors and non-idempotent calls are not retried
# ---------------------------------------------------------------------------


def test_unmarked_error_is_permanent_and_not_retried():
    """An error without the retryable marker (validation / off-allow-list /
    a permanent tool error) is surfaced immediately, never retried."""

    async def permanent(_name, _args):
        raise RuntimeError("endpoint 'nope' is not an allow-listed GET endpoint")

    result, lines = _call(permanent)

    assert isinstance(result, ToolError)
    assert result.reason == "tool_error"
    assert len(_events(lines, "agent.tool_selected")) == 1
    assert len(_events(lines, "agent.retried")) == 0
    [returned] = _events(lines, "agent.tool_returned")
    assert returned["status"] == "error"


def test_non_idempotent_retryable_failure_is_not_retried():
    async def always_fail(_name, _args):
        raise _retryable("transient, but this is a POST")

    result, lines = _call(always_fail, tool_name="http_post", idempotent=False)

    assert isinstance(result, ToolError)
    assert len(_events(lines, "agent.tool_selected")) == 1
    assert len(_events(lines, "agent.retried")) == 0
    [returned] = _events(lines, "agent.tool_returned")
    assert returned["status"] == "error"


# ---------------------------------------------------------------------------
# Metadata-only discipline: raw args never appear; only a digest does
# ---------------------------------------------------------------------------


def test_args_are_digested_not_logged_raw():
    secret_order = "ord-SECRET-9999"

    async def ok(_name, _args):
        return {"status_code": 200, "body": {}, "latency_ms": 1}

    _, lines = _call(ok, args={"endpoint": "get_shipment", "params": {"order_id": secret_order}})

    raw = json.dumps(lines)
    assert secret_order not in raw
    [selected] = _events(lines, "agent.tool_selected")
    assert "args_digest" in selected
    assert secret_order not in selected["args_digest"]
    assert len(selected["args_digest"]) == 12


def test_tool_returned_never_carries_response_body():
    async def ok(_name, _args):
        return {"status_code": 200, "body": {"leak": "SHOULD_NOT_APPEAR"}, "latency_ms": 1}

    _, lines = _call(ok)
    assert "SHOULD_NOT_APPEAR" not in json.dumps(lines)
