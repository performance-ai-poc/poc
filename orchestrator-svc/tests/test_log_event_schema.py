"""Regression coverage for the agent.* log event schema.

Both the architecture design doc and the aligned implementation spec
require agent.* events to be flat JSON: dotted metadata keys like
graph.node and step.sequence sit at the TOP level of the log line, next to
service.name and the four correlation IDs — not nested under a "payload"
key. logging_utils.log_agent_step_started/_completed/_failed used to nest
everything under "payload" (log_event(ctx, event, payload=payload));
they now spread it via **payload so it lands flat. This file locks in the
flat shape directly; tests/test_orchestrator_failure_path.py separately
checks the failed-event case (which additionally carries error_type).
"""

from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient

from app.context import resolve_context
from app.logging_utils import _JsonFormatter, get_logger, log_agent_step_started, log_event
from app.main import app

client = TestClient(app)


def _post_chat_capturing_logs(message: str) -> list[dict]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        client.post("/chat", json={"message": message})
    finally:
        logger.removeHandler(handler)

    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_agent_step_started_event_is_flat_not_nested_under_payload():
    lines = _post_chat_capturing_logs("Can you look up my orders?")
    [step_started] = [line for line in lines if line["event"] == "agent.step_started"]

    assert "payload" not in step_started
    assert step_started["graph.node"] == "route"
    assert step_started["step.sequence"] == 1
    assert step_started["agent"] == "db_agent"
    assert "instruction_digest" in step_started
    # Sits alongside the standard envelope fields, same level, same object.
    assert step_started["service.name"] == "backend-api"
    assert step_started["event"] == "agent.step_started"
    for key in ("run_id", "request_id", "session_id", "tenant_id"):
        assert key in step_started


def test_agent_step_completed_event_is_flat_not_nested_under_payload():
    lines = _post_chat_capturing_logs("Can you look up my orders?")
    [step_completed] = [line for line in lines if line["event"] == "agent.step_completed"]

    assert "payload" not in step_completed
    assert step_completed["graph.node"] == "advance"
    assert step_completed["step.sequence"] == 1
    assert "duration_ms" in step_completed


# ---------------------------------------------------------------------------
# Audit follow-up: log_event(ctx, event, *, endpoint=None, status_code=None,
# level=INFO, **extra) used to reserve "level" as a keyword-only parameter
# name, so a payload dict spread via **extra with its own "level" key (a
# plausible future metadata field — e.g. a business-severity classification,
# distinct from Python's own logging level) raised
# `TypeError: level must be an integer` instead of logging the event. That
# exception was unhandled at every call site, so it propagated out of the
# LangGraph node that triggered it and became an unhandled 500 for the end
# user — a pure observability concern taking down a real request, violating
# the fail-open discipline documented at the top of app/logging_utils.py.
#
# Fix: the parameter is renamed to log_level (see app/logging_utils.py), and
# log_event's whole body is now wrapped in a try/except that can never raise
# — any failure (this collision, or any other future one) is replaced with a
# minimal "logging.emit_failed" fallback line instead. The two tests below
# cover both halves of the fix: the specific collision no longer occurs at
# all (first test), and the defense-in-depth fallback actually engages for
# an unrelated, still-unknown future failure (second test).
# ---------------------------------------------------------------------------


def test_level_key_in_agent_payload_does_not_raise_and_logs_flat():
    """Calling an agent.* emitter with a payload containing a "level" key
    must not raise, and the "level" field must land as an ordinary flat
    metadata field on the log line, not be consumed by log_event's own
    logging-level parameter (which is named log_level precisely to avoid
    this)."""
    ctx = resolve_context()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        log_agent_step_started(
            ctx,
            {
                "graph.node": "route",
                "step.sequence": 1,
                "agent": "db_agent",
                "level": "critical-business-event",
            },
        )
    finally:
        logger.removeHandler(handler)

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    [line] = lines
    assert line["event"] == "agent.step_started"
    assert line["level"] == "critical-business-event"
    assert line["graph.node"] == "route"


def test_level_key_in_agent_payload_does_not_break_real_chat_request(monkeypatch):
    """End-to-end version: a "level" key colliding inside a real agent.*
    emission — triggered through an actual /chat call, not a direct unit
    call — must not turn into a failed request. Monkeypatches the
    log_agent_step_started binding used by route_node (app/orchestrator/
    nodes.py) to inject a "level" key into its payload, simulating a
    future contributor adding such a field, then drives a real /chat call
    and confirms it still succeeds normally.
    """
    import app.orchestrator.nodes as nodes_module

    original_log_agent_step_started = nodes_module.log_agent_step_started

    def _inject_level_key(ctx, payload):
        return original_log_agent_step_started(ctx, {**payload, "level": "simulated-collision"})

    monkeypatch.setattr(nodes_module, "log_agent_step_started", _inject_level_key)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        response = client.post("/chat", json={"message": "Can you look up my orders?"})
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    assert isinstance(response.json().get("reply"), str) and response.json()["reply"]

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    [step_started] = [line for line in lines if line["event"] == "agent.step_started"]
    assert step_started["level"] == "simulated-collision"


def test_log_event_swallows_unexpected_errors_and_emits_fallback_line(monkeypatch):
    """Defense-in-depth: log_event must never raise, even for a failure
    unrelated to the level-key collision above (e.g. some future,
    still-unknown formatting or handler error). Forces the underlying
    Logger.log call to raise once and confirms log_event swallows it and
    emits a minimal "logging.emit_failed" fallback line — carrying no
    payload data from the original call — instead of propagating.
    """
    real_logger = get_logger()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    real_logger.addHandler(handler)

    original_log = real_logger.log
    call_count = {"n": 0}

    def _flaky_log(level, msg, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated logging failure")
        return original_log(level, msg, *args, **kwargs)

    monkeypatch.setattr(real_logger, "log", _flaky_log)
    try:
        log_event(resolve_context(), "some.test.event", foo="bar")
    finally:
        real_logger.removeHandler(handler)

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    [line] = lines
    assert line["event"] == "logging.emit_failed"
    assert line["original_event"] == "some.test.event"
    assert line["error_type"] == "RuntimeError"
    # The fallback carries no data from the original call's payload.
    assert "foo" not in line
