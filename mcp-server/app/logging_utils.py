"""Server-side correlated JSON logging for the MCP server.

Mirrors ``orchestrator-svc/app/logging_utils.py`` in shape and discipline so the
two services' logs line up field-for-field once something is tailing both:

- One JSON object per line to stdout, nothing else.
- ``service.name = "mcp-server"`` on every line (the orchestrator uses
  ``"backend-api"``), so records can be told apart by emitter.
- The four correlation IDs (``run_id / request_id / session_id / tenant_id``)
  on every line, read from the MCP request metadata (``ctx.request_context``)
  and defaulting to ``null`` when the caller didn't supply them. Never a
  ``trace_id``.
- Fail-open: ``log_event`` catches everything and can never raise. A logging
  failure must never take down a tool call — pure observability concern.
- Metadata-only: log lines carry a short *digest* of the tool args, never the
  raw args, and never raw SQL rows, document text, or request/response bodies.
  This is what makes the stream safe to ship to a collector before any
  redaction layer exists.

This module only ever writes to local stdout — no synchronous call to any
external collector — in keeping with the fail-open discipline. The OTel
bootstrap adds a separate LoggingHandler to this logger when OTLP logs are
enabled, without changing any call site.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.config import settings

_LOGGER_NAME = "mcp-server"

# The four correlation IDs, in the exact spelling the orchestrator uses. The
# MCP-client teammate populates these inside the request _meta via
# ClientSession.call_tool(..., meta={...}); RequestParams.Meta is extra="allow",
# so they land as top-level attributes we can read off ctx.request_context.meta.
CORRELATION_KEYS = ("run_id", "request_id", "session_id", "tenant_id")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "json_payload", None)
        if payload is None:
            payload = {"message": record.getMessage()}
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel((level or settings.log_level).upper())
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        configure_logging()
    return logger


def ids_from_ctx(ctx: Any) -> dict[str, str | None]:
    """Extract the four correlation IDs from a FastMCP ``Context``.

    Reads ``ctx.request_context.meta`` (a ``RequestParams.Meta`` with
    ``extra="allow"``) and pulls the four ID keys off it. Anything missing
    becomes ``None`` rather than raising — the server logs correlation IDs when
    the client sent them and ``null`` when it didn't, but must never fail a tool
    call over a missing/oddly-shaped ``_meta``.
    """
    ids: dict[str, str | None] = {k: None for k in CORRELATION_KEYS}
    try:
        meta = ctx.request_context.meta if ctx is not None else None
    except Exception:  # noqa: BLE001 — fail-open: never let ID extraction crash a call.
        meta = None
    if meta is not None:
        for key in CORRELATION_KEYS:
            value = getattr(meta, key, None)
            if isinstance(value, str):
                ids[key] = value
    return ids


def args_digest(args: dict[str, Any]) -> str:
    """A short, stable, non-reversible digest of tool args.

    We log this instead of the raw args so a log line can never leak a raw SQL
    string, a search query, or a request body while still letting an operator
    correlate "same inputs -> same digest" across attempts/retries.
    """
    try:
        blob = json.dumps(args, sort_keys=True, default=str).encode("utf-8")
    except Exception:  # noqa: BLE001
        blob = repr(args).encode("utf-8", "replace")
    return hashlib.sha256(blob).hexdigest()[:16]


def log_event(
    ids: dict[str, str | None],
    event: str,
    *,
    log_level: int = logging.INFO,
    **extra: Any,
) -> None:
    """Emit one structured JSON log line. Never raises (fail-open).

    ``ids`` is the dict returned by :func:`ids_from_ctx`; its four keys are
    stamped on the line alongside ``timestamp / service.name / event`` and any
    ``extra`` metadata. As with the orchestrator, the logging-level parameter is
    named ``log_level`` (not ``level``) so a future ``level`` metadata field
    spread via ``**extra`` can't collide with it.
    """
    try:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service.name": settings.service_name,
            "event": event,
        }
        payload.update({k: ids.get(k) for k in CORRELATION_KEYS})
        payload.update(extra)
        get_logger().log(log_level, event, extra={"json_payload": payload})
    except Exception as exc:  # noqa: BLE001 — logging must never crash a tool call.
        try:
            fallback = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service.name": settings.service_name,
                "event": "logging.emit_failed",
                **{k: (ids.get(k) if isinstance(ids, dict) else None) for k in CORRELATION_KEYS},
                "original_event": event,
                "error_type": type(exc).__name__,
            }
            get_logger().log(logging.ERROR, "logging.emit_failed", extra={"json_payload": fallback})
        except Exception:  # noqa: BLE001 — even the fallback must never raise.
            pass
