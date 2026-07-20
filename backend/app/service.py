"""Placeholder business logic.

This is the seam where a LangGraph orchestrator and real agents get wired in
later. For now it just echoes the incoming message back. Every function here
takes the request's ``RequestContext`` explicitly, and never persists or logs
the raw ``message`` content beyond what's needed to build the reply.

``generate_reply`` is ``async def`` even though nothing here awaits yet: the
real implementation (step 8) will make a blocking model API call, and an
async signature now means that lands without forcing a signature change on
every caller.
"""

from __future__ import annotations

from app.context import RequestContext
from app.logging_utils import log_event


async def generate_reply(message: str, ctx: RequestContext) -> str:
    log_event(ctx, "api.service.generate_reply.start", endpoint="/chat")
    reply = f"Received: {message}"
    log_event(ctx, "api.service.generate_reply.complete", endpoint="/chat")
    return reply
