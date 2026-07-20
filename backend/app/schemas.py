"""Request/response contract for the public API.

This is the schema other components (orchestrator, MCP server, telemetry
layer) integrate against, so it is kept intentionally small and stable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's request text.")

    # All four identifiers are optional on input: a caller may supply any
    # subset (e.g. to resume a session), and anything omitted is generated
    # server-side. See app.context.resolve_context.
    trace_id: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    tenant_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    trace_id: str
    request_id: str
    session_id: str
    tenant_id: str


class ErrorResponse(BaseModel):
    error: str
    detail: str
    trace_id: str
    request_id: str
    session_id: str
    tenant_id: str
