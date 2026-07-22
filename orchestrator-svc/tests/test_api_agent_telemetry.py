"""End-to-end telemetry coverage for the REST API Agent (Agent 2).

Drives a real /chat request that routes to api_agent (offline mode — the test
default) and asserts the full agent.* event contract for the step: the LLM
call, both tool calls, their ordering via call.sequence, and the metadata
fields the dashboard requires (spec sections 4.5, 5.1, 5.2). Also re-checks
the two suite-wide invariants: consistent correlation IDs and no raw message
leak into any log line.
"""

from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient

from app.config import settings
from app.logging_utils import _JsonFormatter, get_logger
from app.main import app

client = TestClient(app)

# A message that routes only to api_agent (the "shipment/status/carrier" rule),
# so the captured events belong to exactly one REST API step. Deliberately
# avoids db-agent keywords like "order"/"policy"/"document".
API_MESSAGE = "Check the shipment status with the carrier please."


def _post_capturing_logs(message: str):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        response = client.post("/chat", json={"message": message})
    finally:
        logger.removeHandler(handler)
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    return response, lines


def _events(lines, name):
    return [line for line in lines if line["event"] == name]


def test_api_step_emits_full_agent_event_sequence():
    response, lines = _post_capturing_logs(API_MESSAGE)
    assert response.status_code == 200

    # Exactly one api_agent step ran.
    [step_started] = _events(lines, "agent.step_started")
    assert step_started["agent"] == "api_agent"
    [step_completed] = _events(lines, "agent.step_completed")
    assert step_completed["step.sequence"] == 1

    # One LLM call (the planner), from the api_agent node.
    [llm_call] = _events(lines, "agent.llm_call")
    assert llm_call["graph.node"] == "api_agent"
    assert llm_call["step.sequence"] == 1
    assert llm_call["model_id"] == settings.llm_model
    assert isinstance(llm_call["input_tokens"], int) and llm_call["input_tokens"] >= 0
    assert isinstance(llm_call["output_tokens"], int) and llm_call["output_tokens"] >= 0
    assert "latency_ms" in llm_call
    assert "call.sequence" in llm_call

    # Two tool calls: list_endpoints then http_get, each a selected/returned pair.
    selected = _events(lines, "agent.tool_selected")
    returned = _events(lines, "agent.tool_returned")
    assert [s["tool_name"] for s in selected] == ["list_endpoints", "http_get"]
    assert [r["tool_name"] for r in returned] == ["list_endpoints", "http_get"]
    assert all(r["status"] == "success" for r in returned)
    assert all("latency_ms" in r for r in returned)
    # The http_get returned carries the response status_code as metadata.
    http_returned = [r for r in returned if r["tool_name"] == "http_get"][0]
    assert http_returned["status_code"] == 200

    # No retries on the happy path.
    assert _events(lines, "agent.retried") == []


def test_call_sequence_is_a_single_running_counter_over_tools_and_llm():
    _, lines = _post_capturing_logs(API_MESSAGE)

    # call.sequence 1 = list_endpoints, 2 = llm_call, 3 = http_get.
    catalog_selected = [s for s in _events(lines, "agent.tool_selected") if s["tool_name"] == "list_endpoints"][0]
    [llm_call] = _events(lines, "agent.llm_call")
    http_selected = [s for s in _events(lines, "agent.tool_selected") if s["tool_name"] == "http_get"][0]

    assert catalog_selected["call.sequence"] == 1
    assert llm_call["call.sequence"] == 2
    assert http_selected["call.sequence"] == 3


def test_all_agent_events_are_flat_and_carry_correlation_ids():
    _, lines = _post_capturing_logs(API_MESSAGE)
    agent_lines = [line for line in lines if line["event"].startswith("agent.")]
    assert agent_lines

    for line in agent_lines:
        assert "payload" not in line
        assert line["service.name"] == "backend-api"
        for key in ("run_id", "request_id", "session_id", "tenant_id"):
            assert key in line
    # One consistent correlation-ID set across the whole request.
    for key in ("run_id", "request_id", "session_id", "tenant_id"):
        assert len({line[key] for line in lines}) == 1


def test_llm_failure_degrades_to_step_failed_not_500(monkeypatch):
    """A failed LLM planning call (e.g. the endpoint is unreachable in live
    mode) must surface as a clean agent.step_failed and a 200 response, never
    an unhandled 500. Regression for the live run where an httpx.ConnectError
    to the model endpoint crashed the whole /chat request."""
    import app.agents.api_agent as api_agent_module
    from app.llm import LLMError

    async def boom(_instruction, _catalog):
        raise LLMError("llm_unreachable")

    monkeypatch.setattr(api_agent_module, "plan_api_calls", boom)

    response, lines = _post_capturing_logs(API_MESSAGE)
    assert response.status_code == 200
    [failed] = _events(lines, "agent.step_failed")
    assert failed["error_type"] == "llm_unreachable"
    # The catalog tool call still happened before planning failed.
    assert any(s["tool_name"] == "list_endpoints" for s in _events(lines, "agent.tool_selected"))
    # The assembled reply reports the failure generically, no traceback.
    reply = response.json()["reply"]
    assert "llm_unreachable" in reply
    assert "Traceback" not in reply


def test_unexpected_agent_error_degrades_to_step_failed(monkeypatch):
    """Defense-in-depth: even an unforeseen (non-classified) error inside the
    agent becomes a terminal step error, not a 500."""
    import app.agents.api_agent as api_agent_module

    async def boom(_instruction, _catalog):
        raise ValueError("some unforeseen bug")

    monkeypatch.setattr(api_agent_module, "plan_api_calls", boom)

    response, lines = _post_capturing_logs(API_MESSAGE)
    assert response.status_code == 200
    [failed] = _events(lines, "agent.step_failed")
    assert failed["error_type"] == "agent_error"


def test_api_step_does_not_leak_raw_message_into_logs():
    marker = "SECRETREF4242"
    message = f"Check the shipment status with the carrier, reference {marker}."
    response, lines = _post_capturing_logs(message)

    assert response.status_code == 200
    assert marker not in json.dumps(lines)
    # But the answer (not a log line) may reference the request text.
    assert isinstance(response.json()["reply"], str)
