"""Shared tool-call retry helper — the single retry layer in the system.

Every MCP tool call made by every sub-agent (app/agents/) goes through
``call_tool_with_retry``. Centralising retries here is a deliberate
architectural decision (spec section 4.3): the retry policy has exactly one
implementation and one shape, so ``agent.retried`` events are uniform and the
orchestrator graph never needs a retry edge — a sub-agent either returns a
final result or a terminal error, and control moves on.

This module owns three of the agent.* telemetry events (spec section 5.2):

  - ``agent.tool_selected``  — before *every* attempt's call (retries included)
  - ``agent.retried``        — before *each* re-attempt (retry.attempt, reason)
  - ``agent.tool_returned``  — on the final response, success or terminal error

Retry contract with the MCP server (mcp-server/app/tools/errors.py):
Only the error *message string* crosses the wire, so the retryable-vs-permanent
signal lives in the text: a failed tool raises, and its message contains
:data:`RETRYABLE_MARKER` iff the failure is transient (transport / 5xx / timeout /
an armed ``fail_next``). Everything else — validation / 4xx, off-allow-list, and
every ``http_post`` failure — is permanent and surfaced immediately. A tool that
*returns* a dict (any ``status_code``, including a 404 "not found") succeeded:
that is data, not an error. Non-idempotent calls (writes / POSTs) are never
retried regardless, since a timed-out write may already have applied.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Awaitable, Callable

from app.context import RequestContext
from app.logging_utils import (
    log_agent_retried,
    log_agent_tool_returned,
    log_agent_tool_selected,
)

# The exact marker the MCP server prepends to a retryable error's message
# (mcp-server/app/tools/errors.py). This is the agreed client<->server contract:
# an error whose text contains this token is retryable; everything else is
# permanent. Kept identical to the server's literal.
RETRYABLE_MARKER = "[RETRYABLE]"

# A tool callable takes (tool_name, args) and returns the tool's result dict, or
# raises on failure. It is the transport seam: app/mcp_client.py supplies the
# real streamable-http implementation (or its offline stub), and tests supply
# fakes.
ToolCallable = Callable[[str, dict], Awaitable[dict]]


class ToolError(Exception):
    """Raised when a tool call fails terminally (after any allowed retries).

    Carries a short, metadata-only ``reason`` category (e.g.
    ``"retryable_tool_error"``, ``"timeout"``, ``"tool_error"``) — never a raw
    exception message, response body, or traceback — so it is safe for a
    sub-agent to surface it into a step-level ``error`` field that eventually
    reaches ``agent.step_failed``.
    """

    def __init__(self, reason: str, *, tool_name: str) -> None:
        super().__init__(f"{tool_name} failed: {reason}")
        self.reason = reason
        self.tool_name = tool_name


# Result metadata that is safe to log on agent.tool_returned: counts, status
# codes, latencies, and stable retrieval IDs only. Response bodies, SQL rows,
# and document text are deliberately excluded (metadata-only discipline,
# spec section 5). Anything not on this allow-list never reaches a log line.
_SAFE_RESULT_METADATA_KEYS = (
    "status_code",
    "row_count",
    "exec_ms",
    "retrieval_ids",
    "count",
    "endpoint_count",
)


def _args_digest(args: dict) -> str:
    """Stable, non-reversible digest of call args — never log raw args, which
    can carry user-derived values (order IDs, query text)."""
    try:
        serialized = json.dumps(args, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 — digest must never break a real call.
        serialized = repr(args)
    return hashlib.sha256(serialized.encode()).hexdigest()[:12]


def _safe_result_metadata(result: dict) -> dict:
    if not isinstance(result, dict):
        return {}
    return {key: result[key] for key in _SAFE_RESULT_METADATA_KEYS if key in result}


def _classify_exception(exc: Exception) -> tuple[bool, str]:
    """Map a tool exception to ``(retryable, reason)``.

    Retryability follows the MCP contract: a transient failure carries
    :data:`RETRYABLE_MARKER` in its message. A raw timeout is treated as
    retryable too (defensive — the transport layer marks these, but a bare
    ``TimeoutError`` should still be retried). Anything else is a permanent
    tool error and must not be retried.
    """
    if RETRYABLE_MARKER in str(exc):
        return True, "retryable_tool_error"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True, "timeout"
    return False, "tool_error"


def _backoff_delay(backoff: tuple[float, ...], attempt: int) -> float:
    """Seconds to wait after ``attempt`` (1-based) before the next attempt.

    Clamps to the last configured value so max_attempts can grow without
    needing a matching-length backoff tuple.
    """
    if not backoff:
        return 0.0
    index = min(attempt - 1, len(backoff) - 1)
    return backoff[index]


async def call_tool_with_retry(
    ctx: RequestContext,
    *,
    graph_node: str,
    step_sequence: int,
    call_sequence: int,
    tool_name: str,
    args: dict,
    tool_callable: ToolCallable,
    idempotent: bool = True,
    max_attempts: int = 3,
    backoff: tuple[float, ...] = (1.0, 2.0),
) -> dict:
    """Call ``tool_name`` via ``tool_callable``, retrying per the policy above.

    Emits ``agent.tool_selected`` before every attempt, ``agent.retried``
    before every re-attempt, and ``agent.tool_returned`` on the final outcome.
    Returns the tool's result dict on success, or raises :class:`ToolError`
    on terminal failure (which the caller turns into a step-level error).

    The positional metadata (graph.node, step.sequence, call.sequence) is
    supplied by the caller so a single log line locates this call precisely
    within the run's execution hierarchy (spec section 2 / 5.1).
    """
    base_meta = {
        "graph.node": graph_node,
        "step.sequence": step_sequence,
        "call.sequence": call_sequence,
        "tool_name": tool_name,
    }
    args_digest = _args_digest(args)

    attempt = 1
    while True:
        log_agent_tool_selected(
            ctx,
            {**base_meta, "args_digest": args_digest, "attempt": attempt},
        )

        started = time.perf_counter()
        try:
            result = await tool_callable(tool_name, args)
        except Exception as exc:  # noqa: BLE001 — classified into a retry decision below.
            latency_ms = int((time.perf_counter() - started) * 1000)
            retryable, reason = _classify_exception(exc)
            if retryable and idempotent and attempt < max_attempts:
                log_agent_retried(ctx, {**base_meta, "retry.attempt": attempt, "reason": reason})
                await asyncio.sleep(_backoff_delay(backoff, attempt))
                attempt += 1
                continue
            log_agent_tool_returned(
                ctx,
                {**base_meta, "latency_ms": latency_ms, "status": "error", "reason": reason},
            )
            raise ToolError(reason, tool_name=tool_name) from exc

        # A returned dict is a successful tool call — any status_code it carries
        # (200/201/404) is data, not a failure. Failures are raised, not
        # returned (see the module docstring / MCP error contract).
        latency_ms = int((time.perf_counter() - started) * 1000)
        log_agent_tool_returned(
            ctx,
            {**base_meta, "latency_ms": latency_ms, "status": "success", **_safe_result_metadata(result)},
        )
        return result
