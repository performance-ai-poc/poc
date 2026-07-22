"""Structured JSON logging.

Every log line is a single JSON object written to stdout. This module never
makes network calls — it is purely local (stdout), in keeping with the
service's fail-open discipline: request handling must never block on an
external logging/telemetry backend. A future OTel layer can tail/parse this
stdout stream (or replace the handler) without changing call sites, because
every call site already passes a ``RequestContext`` explicitly.

Metadata-only by default: log lines carry the four identifiers, the endpoint,
a timestamp, and response status/event name. They deliberately never include
the raw user ``message`` body, so this stays safe to ship to log aggregators
before a redaction layer exists.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.context import RequestContext

_LOGGER_NAME = "backend"

# Stamped on every log line as "service.name" so that once the orchestrator,
# agents, and MCP server are logging in this same shape, records can be told
# apart by which service emitted them (matches the design doc's event
# schema, which carries service.name on every record).
SERVICE_NAME = "backend-api"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "json_payload", None)
        if payload is None:
            payload = {"message": record.getMessage()}
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def log_event(
    ctx: RequestContext,
    event: str,
    *,
    endpoint: str | None = None,
    status_code: int | None = None,
    log_level: int = logging.INFO,
    **extra: Any,
) -> None:
    """Emit one structured JSON log line for a request-scoped event.

    ``ctx`` is required (not optional) so it is impossible to log a
    request-related event without carrying the four identifiers.

    The Python logging level parameter is named ``log_level`` here, not
    ``level`` — a caller's ``**extra`` metadata dict is spread directly
    into this function's keyword arguments (see the ``agent.*`` emitters
    below), and a metadata field named ``level`` is a plausible future
    field name (e.g. a business-severity classification) that would
    otherwise collide with a same-named reserved parameter.

    This function must never raise: a failure here (a collision like the
    one above, or any other unexpected error while building/emitting the
    line) would otherwise propagate out of whatever caller triggered it —
    including a LangGraph node mid-request — turning a pure observability
    concern into a failed customer request. That would violate the
    fail-open discipline documented at the top of this module, so any
    exception is caught and replaced with a minimal fallback line instead
    of ever being re-raised. The fallback intentionally carries no
    ``extra`` payload data (that's the part that may have caused the
    failure) — just enough to know something failed to log.
    """
    try:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service.name": SERVICE_NAME,
            "event": event,
            **ctx.as_dict(),
        }
        if endpoint is not None:
            payload["endpoint"] = endpoint
        if status_code is not None:
            payload["status_code"] = status_code
        payload.update(extra)

        get_logger().log(log_level, event, extra={"json_payload": payload})
    except Exception as exc:  # noqa: BLE001 — fail-open: logging must never crash a request.
        try:
            fallback_payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service.name": SERVICE_NAME,
                "event": "logging.emit_failed",
                **ctx.as_dict(),
                "original_event": event,
                "error_type": type(exc).__name__,
            }
            get_logger().log(logging.ERROR, "logging.emit_failed", extra={"json_payload": fallback_payload})
        except Exception:  # noqa: BLE001 — even the fallback must never raise.
            pass


# agent.* events — owned by the orchestrator's graph nodes (see
# app/orchestrator/nodes.py), alongside the api.* events emitted by
# main.py/middleware.py. Dotted metadata keys (graph.node,
# step.sequence) aren't valid Python keyword-argument *syntax* (you can't
# write log_event(graph.node=...)), but a dict with dotted string keys can
# still be spread via **payload — Python's **kwargs unpacking only requires
# string keys, not valid identifiers. Each emitter spreads its metadata dict
# into log_event's **extra so the fields land at the TOP level of the JSON
# line (alongside service.name, event, and the four correlation IDs), per
# the documented event schema — never nested under a "payload" key.
#
# agent.tool_selected, agent.tool_returned, agent.retried, and
# agent.llm_call are owned by the sub-agents (app/agents/) and the shared
# retry helper (app/retry.py) — the executor layer, where the only LLM and
# tool calls in the system happen. They are defined below alongside the
# step.* emitters so the whole agent.* namespace lives in one module and
# shares log_event's fail-open + metadata-only guarantees by construction.


def log_agent_step_started(ctx: RequestContext, payload: dict) -> None:
    """Emitted by the orchestrator's route node before dispatching a step."""
    log_event(ctx, "agent.step_started", **payload)


def log_agent_step_completed(ctx: RequestContext, payload: dict) -> None:
    """Emitted by the orchestrator's advance node when a step succeeded."""
    log_event(ctx, "agent.step_completed", **payload)


def log_agent_step_failed(ctx: RequestContext, payload: dict) -> None:
    """Emitted by the orchestrator's advance node when a step failed."""
    log_event(ctx, "agent.step_failed", log_level=logging.WARNING, **payload)


def log_agent_llm_call(ctx: RequestContext, payload: dict) -> None:
    """Emitted by a sub-agent around each LLM call it makes.

    Carries model/token/latency metadata only (model_id, input_tokens,
    output_tokens, latency_ms) plus the graph.node/step.sequence/call.sequence
    positional metadata — never the prompt or the model's response text. The
    orchestrator makes no LLM calls, so every agent.llm_call originates in a
    sub-agent (architecture spec, section 3.2 / 5.1).
    """
    log_event(ctx, "agent.llm_call", **payload)


def log_agent_tool_selected(ctx: RequestContext, payload: dict) -> None:
    """Emitted by the shared retry helper immediately before each MCP call
    attempt (including retries). Carries tool_name + args_digest — the args
    themselves are hashed, never logged raw."""
    log_event(ctx, "agent.tool_selected", **payload)


def log_agent_tool_returned(ctx: RequestContext, payload: dict) -> None:
    """Emitted by the shared retry helper when an MCP call returns (on final
    success or terminal failure). Carries latency_ms, a success/error status,
    and result *metadata* only (status_code, counts, retrieval_ids) — never
    response bodies, SQL rows, or document text."""
    log_event(ctx, "agent.tool_returned", **payload)


def log_agent_retried(ctx: RequestContext, payload: dict) -> None:
    """Emitted by the shared retry helper before each re-attempt — the single
    retry layer in the whole system (architecture spec, section 4.3). Carries
    retry.attempt (1 = first retry) and a short reason string."""
    log_event(ctx, "agent.retried", log_level=logging.WARNING, **payload)
