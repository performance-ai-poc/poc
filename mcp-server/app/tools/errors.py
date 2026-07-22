"""Error taxonomy for MCP tools.

``RetryableToolError`` does **not** exist in ``mcp==1.28.1`` — the SDK only ships
``ToolError`` (``mcp.server.fastmcp.exceptions``). We re-export that as our
permanent-failure type and define ``RetryableToolError`` here.

Why a marker in the message instead of relying on the class? FastMCP funnels
*every* exception a tool raises through
``ToolError(f"Error executing tool {name}: {e}")``, and the low-level server
then returns ``CallToolResult(isError=True, content=[TextContent(text=str(e))])``.
Only the message **string** crosses the wire — the client never sees the Python
class. So the retryable-vs-permanent signal has to live in the text. Every
``RetryableToolError`` embeds :data:`RETRYABLE_MARKER`; the agreed contract with
the MCP-client teammate is: *an error result whose text contains the marker is
retryable; everything else is permanent.*

Taxonomy (design §Locked decisions):
- ``RetryableToolError`` — transport / 5xx / timeout, and the ``fail_next``
  simulated failure. The client's retry helper recovers these.
- ``ToolError`` — validation / 4xx, ``run_query`` SELECT-guard violations, and
  all ``http_post`` failures (non-idempotent, must never be retried).
"""

from __future__ import annotations

# Re-exported so tools import both error types from one place, and so the type
# our tools raise for permanent failures IS FastMCP's own ToolError (its message
# passes straight through to the client).
from mcp.server.fastmcp.exceptions import ToolError

# Stable, greppable token the MCP-client retry helper keys on. Kept short and
# bracketed so it survives FastMCP's own message wrapping intact.
RETRYABLE_MARKER = "[RETRYABLE]"


class RetryableToolError(ToolError):
    """A transient tool failure the caller may retry.

    Subclasses ``ToolError`` so FastMCP treats it as a tool error (its message
    reaches the client) rather than masking it as a generic internal error. The
    marker is prepended to the message so it's detectable after FastMCP wraps
    the exception.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        # Attributes are for server-side logging only; only the message string
        # travels to the client.
        self.retryable = True
        self.code = code
        self.retry_after_ms = retry_after_ms
        super().__init__(f"{RETRYABLE_MARKER} {message}")


__all__ = ["ToolError", "RetryableToolError", "RETRYABLE_MARKER"]
