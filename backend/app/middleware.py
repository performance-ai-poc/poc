"""Per-request identity resolution and access logging.

This middleware runs before route handling and before pydantic validation of
the request body. That ordering matters: it guarantees a ``RequestContext``
is attached to every request — including ones whose body is malformed or
missing the ``message`` field — so error responses can still carry
consistent trace/request/session/tenant IDs for telemetry to pick up.
"""

from __future__ import annotations

import json
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.context import resolve_context
from app.logging_utils import log_event


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        ids = await self._peek_ids(request)
        ctx = resolve_context(**ids)
        request.state.context = ctx

        start = time.perf_counter()
        log_event(ctx, "request.started", endpoint=request.url.path)

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(
            ctx,
            "request.completed",
            endpoint=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response

    @staticmethod
    async def _peek_ids(request: Request) -> dict:
        """Best-effort extraction of caller-supplied IDs from the JSON body.

        Reading the body here does not consume it: Starlette caches the raw
        bytes on the request, so downstream pydantic parsing still sees the
        full body. Any failure to parse (malformed JSON, non-JSON body, no
        body at all) is swallowed — missing IDs are simply generated fresh.
        """
        keys = ("trace_id", "request_id", "session_id", "tenant_id")
        try:
            raw = await request.body()
            if not raw:
                return {}
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {}
            return {k: data[k] for k in keys if isinstance(data.get(k), str) and data.get(k)}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
