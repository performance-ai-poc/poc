"""Request/response contract for the public API.

This is the schema other components (orchestrator, MCP server, telemetry
layer) integrate against, so it is kept intentionally small and stable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.context import ID_MAX_LENGTH, ID_PATTERN


class ChatRequest(BaseModel):
    # max_length is explicit and generous rather than relying on the
    # middleware's 100KB body-size cap (app.middleware.MAX_BODY_BYTES) to
    # bound this incidentally — that cap exists for a different reason
    # (protecting the server from oversized payloads generally).
    message: str = Field(..., min_length=1, max_length=10_000, description="The user's request text.")

    # session_id and tenant_id are optional on input: a caller may supply
    # either (e.g. to resume a session), and anything omitted is generated
    # server-side. See app.context.resolve_context. Bounded length + a safe
    # charset (letters, numbers, hyphens, underscores) so a caller can't hand
    # us something oversized or binary that later becomes an OTel attribute.
    #
    # run_id and request_id are deliberately NOT accepted here: they are
    # always server-generated (see app.middleware.RequestContextMiddleware),
    # so a caller can't reuse/spoof them across unrelated requests. Pydantic's
    # default extra="ignore" means a caller who sends them anyway just has
    # those values silently dropped, not rejected.
    session_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH, pattern=ID_PATTERN)
    tenant_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH, pattern=ID_PATTERN)


class ChatResponse(BaseModel):
    reply: str
    run_id: str
    request_id: str
    session_id: str
    tenant_id: str


class ErrorResponse(BaseModel):
    error: str
    detail: str
    run_id: str
    request_id: str
    session_id: str
    tenant_id: str
