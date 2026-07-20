"""Placeholder business logic.

This is the seam where a LangGraph orchestrator and real agents get wired in
later. For now it just echoes the incoming message back. Every function here
takes the request's ``RequestContext`` explicitly, and never persists or logs
the raw ``message`` content beyond what's needed to build the reply.
"""

from __future__ import annotations

from app.context import RequestContext
from app.logging_utils import log_event


def generate_reply(message: str, ctx: RequestContext) -> str:
    log_event(ctx, "service.generate_reply.start", endpoint="/chat")
    reply = f"Received: {message}"
    log_event(ctx, "service.generate_reply.complete", endpoint="/chat")
    return reply
