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

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestContext:
    trace_id: str
    request_id: str
    session_id: str
    tenant_id: str

    def as_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
        }


def new_id() -> str:
    return str(uuid.uuid4())


def resolve_context(
    trace_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    tenant_id: str | None = None,
) -> RequestContext:
    """Fill in any missing identifiers with freshly generated UUIDs.

    Callers may supply any subset of the four IDs (e.g. a caller resuming a
    session passes ``session_id`` but not the rest); anything missing is
    generated here so every request is fully identified before any business
    logic or logging runs.
    """
    return RequestContext(
        trace_id=trace_id or new_id(),
        request_id=request_id or new_id(),
        session_id=session_id or new_id(),
        tenant_id=tenant_id or new_id(),
    )
