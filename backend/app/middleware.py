"""Per-request identity resolution and access logging.

This middleware runs before route handling and before pydantic validation of
the request body. That ordering matters: it guarantees a ``RequestContext``
is attached to every request — including ones whose body is malformed or
missing the ``message`` field — so error responses can still carry
consistent run/request/session/tenant IDs for telemetry to pick up.

run_id and request_id are always minted here (never taken from the caller);
only session_id and tenant_id are peeked from the caller's body. See
``_peek_ids`` below.

``/health`` and any ``OPTIONS`` (CORS preflight) request bypass this
middleware entirely — see ``_skips_context``. Neither reaches real business
logic, so minting IDs and logging ``api.request.*`` for them would just be
noise indistinguishable from real traffic once something is tailing these
logs — e.g. a Kubernetes liveness probe polling ``/health`` every few
seconds would otherwise mint a fresh fake "session" on every poll.
"""

from __future__ import annotations

import json
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.context import is_valid_id, resolve_context
from app.logging_utils import log_event
from app.schemas import ErrorResponse

# Applies globally (this middleware wraps every route), not just /chat, so any
# future endpoint is protected before its body is ever read.
MAX_BODY_BYTES = 100 * 1024  # 100 KB


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._skips_context(request):
            return await call_next(request)

        body = await self._read_body_capped(request)
        if body is None:
            ctx = resolve_context()
            log_event(
                ctx,
                "api.request.body_too_large",
                endpoint=request.url.path,
                status_code=413,
                level=logging.WARNING,
            )
            error_body = ErrorResponse(
                error="payload_too_large",
                detail=f"Request body exceeds the {MAX_BODY_BYTES}-byte limit.",
                **ctx.as_dict(),
            )
            return JSONResponse(status_code=413, content=error_body.model_dump())

        ids = self._peek_ids(body)
        ctx = resolve_context(**ids)
        request.state.context = ctx

        start = time.perf_counter()
        log_event(ctx, "api.request.started", endpoint=request.url.path)

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(
            ctx,
            "api.request.completed",
            endpoint=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response

    @staticmethod
    def _skips_context(request: Request) -> bool:
        """True for requests that never reach real business logic.

        ``/health`` is a dependency-free liveness check and a CORS preflight
        never reaches a route handler at all (``CORSMiddleware`` answers it
        directly) — neither needs request-scoped IDs or an
        api.request.started/completed log line, and both are polled/sent far
        more often than real traffic once something is monitoring the
        service.
        """
        return request.method == "OPTIONS" or request.url.path == "/health"

    @staticmethod
    async def _read_body_capped(request: Request) -> bytes | None:
        """Read the full request body, or return None if it exceeds MAX_BODY_BYTES.

        This does NOT rely on the Content-Length header to enforce the cap —
        a request with no Content-Length at all (e.g. chunked
        transfer-encoding) has nothing to check there, so a header-only guard
        can be bypassed simply by omitting it. Instead, bytes are counted as
        they arrive off the ASGI stream and the read is aborted the moment the
        running total exceeds the limit, so the cap holds regardless of what
        (if anything) the client declared.

        A present, valid, in-bounds Content-Length is still checked first as
        a cheap short-circuit for the common case of an honestly-labeled
        oversized request — this avoids reading a large body off the socket
        at all when the client has already told us it's too big.

        The bytes read here are cached onto ``request._body`` (Starlette's
        own caching attribute), so this does not consume the body: downstream
        pydantic parsing still sees the full content via the same cache
        instead of re-reading the (already-drained) ASGI stream.
        """
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and (declared < 0 or declared > MAX_BODY_BYTES):
                return None

        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                return None
            chunks.append(chunk)

        body = b"".join(chunks)
        request._body = body  # noqa: SLF001 — Starlette's own cache attribute; see docstring.
        return body

    @staticmethod
    def _peek_ids(body: bytes) -> dict:
        """Best-effort extraction of caller-supplied IDs from the JSON body.

        Only session_id and tenant_id are peeked here. run_id and request_id
        are deliberately excluded — they are always server-generated (see
        module docstring / app.context), so any run_id/request_id a caller
        sends is ignored rather than honored, even if it's well-formed.

        Any failure to parse (malformed JSON, non-JSON body, no body at all)
        is swallowed — missing IDs are simply generated fresh. IDs that don't
        pass ``is_valid_id`` (too long, bad characters) are likewise dropped
        here; the real 422 for an invalid session_id/tenant_id comes from
        ChatRequest's own field validation once pydantic runs.
        """
        keys = ("session_id", "tenant_id")
        if not body:
            return {}
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: data[k] for k in keys if isinstance(data.get(k), str) and is_valid_id(data[k])}
