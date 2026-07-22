"""Tool registration + cross-cutting instrumentation.

``register(mcp)`` is what ``app/server.py`` calls. It wraps each tool handler
*once* with everything that must happen on every call, then registers it via
``mcp.tool()``:

- read the four correlation IDs off ``ctx`` and emit ``mcp.request`` /
  ``mcp.response`` (or ``mcp.error``) JSON logs — metadata-only, fail-open;
- honor ``MOCK_TOOL_LATENCY_MS``;
- call ``maybe_fail(name)`` so an armed ``fail_next`` fires uniformly;
- classify the outcome (retryable vs permanent) for the error log line.

The exception->RetryableToolError/ToolError *mapping* lives in the handlers
(they know which psycopg/HTTP condition means what); this layer just logs and
re-raises so FastMCP surfaces the error to the client.

**Why the wrapper preserves the signature the way it does:** FastMCP builds a
tool's input schema from ``inspect.signature(fn, eval_str=True)`` and finds the
``Context`` parameter via ``typing.get_type_hints(fn)``. So the wrapper resolves
the wrapped handler's hints *once* (against the handler's own module globals,
where ``Context``/``list``/``dict`` are defined) and stamps the resolved
``__signature__`` and ``__annotations__`` onto itself. FastMCP then sees the
real parameters (``ctx`` excluded from the schema, correctly injected) even
though the wrapper physically lives in this module.
"""

import asyncio
import functools
import inspect
import logging
import time
import typing

from mcp.server.fastmcp import Context, FastMCP

from app.config import settings
from app.logging_utils import args_digest, ids_from_ctx, log_event
from app.tools import control
from app.tools.control import fail_next
from app.tools.db_tools import get_schema, run_query, search_documents
from app.tools.errors import RETRYABLE_MARKER, RetryableToolError
from app.tools.http_tools import http_get, http_post, list_endpoints

# Keys we lift out of a tool's result dict into the mcp.response log line. All
# are safe, non-PII metadata (counts, timings, status codes, opaque chunk IDs) —
# never rows, document text, or bodies.
_RESPONSE_META_KEYS = ("row_count", "exec_ms", "latency_ms", "count", "status_code", "retrieval_ids", "armed")


def _is_context_annotation(annotation) -> bool:
    try:
        if inspect.isclass(annotation) and issubclass(annotation, Context):
            return True
    except TypeError:
        pass
    for arg in typing.get_args(annotation):
        try:
            if inspect.isclass(arg) and issubclass(arg, Context):
                return True
        except TypeError:
            continue
    return False


def _find_ctx_param(hints: dict) -> str | None:
    for name, annotation in hints.items():
        if name == "return":
            continue
        if _is_context_annotation(annotation):
            return name
    return None


def _resolved_signature(fn, hints: dict) -> inspect.Signature:
    sig = inspect.signature(fn)
    params = [p.replace(annotation=hints.get(p.name, p.annotation)) for p in sig.parameters.values()]
    return sig.replace(parameters=params, return_annotation=hints.get("return", sig.return_annotation))


def instrument(name: str):
    """Wrap a tool handler with the shared request/response/error logging,
    latency injection, and fail_next hook. Returns a FastMCP-registrable callable."""

    def decorator(fn):
        hints = typing.get_type_hints(fn)
        ctx_param = _find_ctx_param(hints)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            ctx = kwargs.get(ctx_param) if ctx_param else None
            ids = ids_from_ctx(ctx)
            # Digest args only — never log the raw SQL / query / body / params.
            call_args = {k: v for k, v in kwargs.items() if k != ctx_param}
            log_event(ids, "mcp.request", tool=name, args_digest=args_digest(call_args))

            start = time.perf_counter()
            try:
                if settings.mock_tool_latency_ms > 0:
                    await asyncio.sleep(settings.mock_tool_latency_ms / 1000)
                # Every tool but the injector itself honors an armed fail_next.
                if name != "fail_next":
                    control.maybe_fail(name)

                result = fn(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result

                duration_ms = round((time.perf_counter() - start) * 1000, 2)
                response_meta = {
                    k: result[k]
                    for k in _RESPONSE_META_KEYS
                    if isinstance(result, dict) and k in result
                }
                log_event(
                    ids,
                    "mcp.response",
                    tool=name,
                    status="success",
                    duration_ms=duration_ms,
                    **response_meta,
                )
                return result
            except Exception as exc:  # noqa: BLE001 — log then re-raise for FastMCP.
                duration_ms = round((time.perf_counter() - start) * 1000, 2)
                retryable = isinstance(exc, RetryableToolError) or (RETRYABLE_MARKER in str(exc))
                log_event(
                    ids,
                    "mcp.error",
                    log_level=logging.WARNING,
                    tool=name,
                    status="error",
                    duration_ms=duration_ms,
                    error_type=type(exc).__name__,
                    retryable=retryable,
                )
                raise

        # Make FastMCP see the handler's real parameters (see module docstring).
        wrapper.__annotations__ = dict(hints)
        wrapper.__signature__ = _resolved_signature(fn, hints)
        return wrapper

    return decorator


# Instrumented tools, built once. Exposed as a mapping so tests can drive the
# *instrumented* path (logging + fail_next) directly, not just the bare handler.
_HANDLERS = (
    ("get_schema", get_schema),
    ("run_query", run_query),
    ("search_documents", search_documents),
    ("list_endpoints", list_endpoints),
    ("http_get", http_get),
    ("http_post", http_post),
    ("fail_next", fail_next),
)

TOOLS = {name: instrument(name)(fn) for name, fn in _HANDLERS}


def register(mcp: FastMCP) -> None:
    """Register all seven instrumented tools on the FastMCP instance."""
    for name, tool_fn in TOOLS.items():
        mcp.tool(name=name)(tool_fn)


__all__ = ["register", "instrument", "TOOLS"]
