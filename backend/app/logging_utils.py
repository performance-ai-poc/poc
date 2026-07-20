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
    level: int = logging.INFO,
    **extra: Any,
) -> None:
    """Emit one structured JSON log line for a request-scoped event.

    ``ctx`` is required (not optional) so it is impossible to log a
    request-related event without carrying the four identifiers.
    """
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **ctx.as_dict(),
    }
    if endpoint is not None:
        payload["endpoint"] = endpoint
    if status_code is not None:
        payload["status_code"] = status_code
    payload.update(extra)

    get_logger().log(level, event, extra={"json_payload": payload})
