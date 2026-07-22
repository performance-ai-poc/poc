"""``fail_next`` simulation state (demo scenario 5).

``FAIL_STATE`` maps a tool name to the number of *remaining* simulated failures
armed for it. Every instrumented tool calls :func:`maybe_fail` at entry (wired
in ``app/tools/__init__.py``); if the tool is armed, the counter is decremented
and a :class:`RetryableToolError` (a simulated 503/timeout) is raised. The
client's retry helper then recovers on the next attempt, producing the
``tool_selected -> retried -> tool_returned success`` shape the demo wants.

This is a *tool-layer* failure injector, deliberately distinct from the
orchestrator's ``__FORCE_*_FAILURE__`` graph-test triggers (which force a
*stub agent* to fail so the graph's abort path can be exercised). This one
forces a *real MCP tool* to fail transiently.
"""

from mcp.server.fastmcp import Context

from app.tools.errors import RetryableToolError

# Module-level, process-wide. A POC single-process server; not shared across
# replicas (fine — fail_next is a demo affordance, not production state).
FAIL_STATE: dict[str, int] = {}


def arm(tool: str, count: int = 1) -> int:
    """Arm ``count`` additional simulated failures for ``tool``. Returns the new total."""
    if count < 0:
        raise ValueError("count must be >= 0")
    FAIL_STATE[tool] = FAIL_STATE.get(tool, 0) + count
    return FAIL_STATE[tool]


def maybe_fail(tool: str) -> None:
    """Raise a simulated transient failure if ``tool`` is armed, else return.

    Decrements the armed counter first, so exactly ``count`` consecutive calls
    fail and the next one is allowed through — that's what lets a single retry
    recover.
    """
    remaining = FAIL_STATE.get(tool, 0)
    if remaining > 0:
        FAIL_STATE[tool] = remaining - 1
        raise RetryableToolError(
            f"simulated transient failure for tool '{tool}' (armed via fail_next)",
            code="SIMULATED_503",
            retry_after_ms=50,
        )


def reset() -> None:
    """Clear all armed failures (used by tests for isolation)."""
    FAIL_STATE.clear()


async def fail_next(tool: str, count: int = 1, ctx: Context = None) -> dict:
    """Arm ``count`` simulated transient failures for a tool (demo scenario 5).

    The next ``count`` calls to ``tool`` raise a retryable error (simulated 503),
    then calls succeed normally. Use this to demonstrate the client's retry path:
    e.g. ``fail_next("http_get")`` then a shipment lookup that fails once and
    succeeds on retry.

    Args:
        tool: Name of the tool to arm (e.g. ``"http_get"``).
        count: How many upcoming calls should fail. Defaults to 1.

    Returns:
        ``{"armed": <remaining failures for this tool>, "tool": tool, "count": count}``
    """
    armed = arm(tool, count)
    return {"armed": armed, "tool": tool, "count": count}
