"""Request-scoped identity context.

A ``RequestContext`` is created once per incoming request (see
``app.middleware.RequestContextMiddleware``) and then passed explicitly as an
argument to every internal function and log call involved in handling that
request. This is deliberate: a future OpenTelemetry / telemetry layer depends
on these four identifiers being present and consistent everywhere, so they
are threaded through the call graph rather than relying solely on implicit
global/contextvar state.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.config import settings

# Shared by app.schemas (request validation) and app.middleware (best-effort
# body peek), so a caller-supplied ID is judged by exactly one rule everywhere
# it's checked. Keeps IDs safe to attach as OTel span attributes later: bounded
# length, no control/binary characters.
ID_MAX_LENGTH = 128
ID_PATTERN = rf"^[A-Za-z0-9_-]{{1,{ID_MAX_LENGTH}}}$"
_ID_RE = re.compile(ID_PATTERN)


def is_valid_id(value: str) -> bool:
    return bool(_ID_RE.fullmatch(value))


@dataclass(frozen=True, slots=True)
class RequestContext:
    # Our own business-level correlation ID for "this one end-to-end
    # request" — not a W3C trace_id. A real OTel trace_id/span_id will be
    # minted by the SDK once tracing instrumentation is wired in; this
    # run_id will be attached to those spans as a correlation attribute.
    run_id: str
    request_id: str
    session_id: str
    tenant_id: str

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
        }


def new_id() -> str:
    return str(uuid.uuid4())


def resolve_context(
    run_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    tenant_id: str | None = None,
) -> RequestContext:
    """Fill in any missing identifiers.

    A caller (HTTP request body) may supply ``session_id`` and/or
    ``tenant_id`` — e.g. a caller resuming a session passes ``session_id``
    but not the rest. ``run_id`` and ``request_id`` are server-owned:
    ``app.middleware.RequestContextMiddleware`` never passes caller-supplied
    values for those two, so they are always freshly generated per request.
    ``session_id`` falls back to a fresh random UUID (each anonymous
    conversation is genuinely distinct). ``tenant_id`` falls back to
    ``settings.default_tenant_id`` instead — a stable value, not a random
    one — so that anonymous/demo traffic groups under one consistent tenant
    rather than every omitted-tenant_id request becoming its own singleton
    tenant that can never correlate with anything else.
    """
    return RequestContext(
        run_id=run_id or new_id(),
        request_id=request_id or new_id(),
        session_id=session_id or new_id(),
        tenant_id=tenant_id or settings.default_tenant_id,
    )
