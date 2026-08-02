"""Coverage for the orchestrator's abort-on-first-failure path.

Both DB Agent and REST API Agent are stubs that normally always succeed, so
without a deliberate way to force a failure, the failure/abort/error-schema
behavior in app/orchestrator/nodes.py had never been exercised. These tests
use each stub's TEST-ONLY FORCE_FAILURE_TRIGGER (see app/agents/db_agent.py
and app/agents/api_agent.py) to make a specific step fail on demand, then
verify:

  - subsequent steps are never dispatched (abort-on-first-failure)
  - state["errors"] is populated with structured, metadata-only detail
  - agent.step_failed carries error_type at the top level of the JSON line
    (flat, per the corrected log schema — see test_log_event_schema.py)
  - the final answer's status is "failed" and says so, without leaking raw
    error/exception detail
  - pick_agent raises rather than silently misrouting on an unrecognized
    agent value

Some assertions require inspecting the full RunState (errors, status),
which the /chat HTTP contract doesn't expose (it only returns "reply" and
the four IDs) — those go through compiled_graph.ainvoke() directly, the
same way app/service.py does. The rest go through the real /chat endpoint,
consistent with the rest of this test suite.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.agents.api_agent import FORCE_FAILURE_TRIGGER as API_AGENT_FAILURE_TRIGGER
from app.agents.db_agent import FORCE_FAILURE_TRIGGER as DB_AGENT_FAILURE_TRIGGER
from app.context import resolve_context
from app.logging_utils import _JsonFormatter, get_logger
from app.main import app
from app.orchestrator.graph import compiled_graph
from app.orchestrator.routing import pick_agent
from app.orchestrator.state import RunState

client = TestClient(app)


def _post_chat_capturing_logs(message: str) -> tuple[object, list[dict]]:
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


def _run_graph_directly(message: str) -> RunState:
    """Invoke the compiled graph the same way app/service.py does, but keep
    the full final state (service.py discards everything except "answer")."""
    initial_state: RunState = {
        "ctx": resolve_context(),
        "message": message,
        "config": {},
        "steps": [],
        "current_step": 0,
        "step_results": {},
        "errors": [],
        "aborted": False,
        "answer": None,
        "status": "running",
    }
    return asyncio.run(compiled_graph.ainvoke(initial_state))


# ---------------------------------------------------------------------------
# (a) Abort-on-first-failure: subsequent steps are not executed
# ---------------------------------------------------------------------------


def test_failed_first_step_aborts_before_second_step_via_http():
    message = f"Check my orders and then check shipment status {DB_AGENT_FAILURE_TRIGGER}"
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200

    step_started = [line for line in lines if line["event"] == "agent.step_started"]
    step_completed = [line for line in lines if line["event"] == "agent.step_completed"]
    step_failed = [line for line in lines if line["event"] == "agent.step_failed"]

    # Only step 1 (db_agent) was ever dispatched — step 2 (api_agent) never
    # started, because the graph aborted immediately after step 1 failed.
    assert len(step_started) == 1
    assert step_started[0]["agent"] == "db_agent"
    assert len(step_completed) == 0
    assert len(step_failed) == 1

    reply = response.json()["reply"]
    # The api_agent step's canned instruction text must never appear — it
    # was never dispatched.
    assert "Check shipment status via the external API." not in reply


def test_failed_middle_step_aborts_before_third_step_via_http():
    """Same as above but the failure happens on step 2 of a 3-step plan,
    confirming abort-on-first-failure works mid-sequence, not just when the
    very first step fails."""
    message = (
        "I have a problem with my orders, please check the shipment status, "
        f"and also see the escalation policy. {API_AGENT_FAILURE_TRIGGER}"
    )
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200

    step_started = [line for line in lines if line["event"] == "agent.step_started"]
    step_completed = [line for line in lines if line["event"] == "agent.step_completed"]
    step_failed = [line for line in lines if line["event"] == "agent.step_failed"]

    # Steps 1 (db_agent, succeeds) and 2 (api_agent, forced failure) run;
    # step 3 (db_agent again, document search) never does.
    assert [line["agent"] for line in step_started] == ["db_agent", "api_agent"]
    assert len(step_completed) == 1
    assert len(step_failed) == 1

    reply = response.json()["reply"]
    # The agents strip the canned per-rule directive before working, so assert
    # on what each step actually produced. Step 3 was the document search and
    # never ran, so no document summary may appear; step 1's DB result must
    # still be there, because abort keeps completed work.
    assert "relevant document" not in reply
    assert "matching record" in reply
    assert "step 2 failed" in reply


# ---------------------------------------------------------------------------
# (b) state["errors"] is populated correctly
# ---------------------------------------------------------------------------


def test_state_errors_populated_on_failure():
    message = f"Check my orders and then check shipment status {DB_AGENT_FAILURE_TRIGGER}"
    final_state = _run_graph_directly(message)

    assert final_state["errors"] == [
        {
            "step_key": "step-1",
            "agent": "db_agent",
            "step.sequence": 1,
            "error_type": "simulated_tool_failure",
        }
    ]
    assert final_state["aborted"] is True
    assert final_state["steps"][0]["status"] == "error"
    # Step 2 was never dispatched, so it's still in its initial "pending" state.
    assert final_state["steps"][1]["status"] == "pending"


def test_no_errors_recorded_when_every_step_succeeds():
    final_state = _run_graph_directly("Can you look up my orders?")
    assert final_state["errors"] == []
    assert final_state["aborted"] is False
    assert final_state["status"] == "completed"


# ---------------------------------------------------------------------------
# (c) agent.step_failed carries a real error type at the TOP level (flat)
# ---------------------------------------------------------------------------


def test_step_failed_event_carries_error_type_at_top_level():
    message = f"Check my orders and then check shipment status {DB_AGENT_FAILURE_TRIGGER}"
    _, lines = _post_chat_capturing_logs(message)

    [failed_event] = [line for line in lines if line["event"] == "agent.step_failed"]

    # Flat schema (fix for the nested "payload" bug): these fields sit
    # directly on the log line, next to service.name/run_id/etc. — not
    # nested under a "payload" key.
    assert "payload" not in failed_event
    assert failed_event["graph.node"] == "advance"
    assert failed_event["step.sequence"] == 1
    assert failed_event["error_type"] == "simulated_tool_failure"
    assert failed_event["service.name"] == "backend-api"
    for key in ("run_id", "request_id", "session_id", "tenant_id"):
        assert key in failed_event


# ---------------------------------------------------------------------------
# (d) Final status is "failed", answer says so, without leaking raw detail
# ---------------------------------------------------------------------------


def test_final_status_is_failed_and_answer_mentions_failure_generically():
    message = f"Check my orders and then check shipment status {DB_AGENT_FAILURE_TRIGGER}"
    final_state = _run_graph_directly(message)

    assert final_state["status"] == "failed"
    assert "step 1 failed" in final_state["answer"]
    assert "simulated_tool_failure" in final_state["answer"]
    # Metadata-only: a category string is fine, a raw exception/traceback is not.
    assert "Traceback" not in final_state["answer"]


def test_completed_steps_before_the_failure_are_still_included_in_the_answer():
    message = (
        "I have a problem with my orders, please check the shipment status, "
        f"and also see the escalation policy. {API_AGENT_FAILURE_TRIGGER}"
    )
    final_state = _run_graph_directly(message)

    assert final_state["status"] == "failed"
    # Step 1 (db_agent) succeeded before step 2 (api_agent) failed — its
    # result must still show up in the assembled answer, not be discarded.
    # The DB agent produces its own record summary; the stub text this used to
    # look for is long gone.
    assert "matching record" in final_state["answer"]
    assert "step 2 failed: simulated_tool_timeout" in final_state["answer"]


# ---------------------------------------------------------------------------
# pick_agent raises on an unrecognized agent value instead of misrouting
# ---------------------------------------------------------------------------


def test_pick_agent_raises_on_unrecognized_agent_value():
    bad_state = {
        "steps": [{"key": "step-1", "agent": "mystery_agent", "instruction": "x", "sequence": 1, "status": "pending"}],
        "current_step": 0,
        "aborted": False,
    }
    with pytest.raises(ValueError, match="mystery_agent"):
        pick_agent(bad_state)


def test_pick_agent_still_returns_done_when_aborted_regardless_of_remaining_steps():
    state_with_pending_work_but_aborted = {
        "steps": [
            {"key": "step-1", "agent": "db_agent", "instruction": "x", "sequence": 1, "status": "error"},
            {"key": "step-2", "agent": "api_agent", "instruction": "y", "sequence": 2, "status": "pending"},
        ],
        "current_step": 1,
        "aborted": True,
    }
    assert pick_agent(state_with_pending_work_but_aborted) == "done"


def test_pick_agent_valueerror_surfaces_as_clean_500_via_real_chat(monkeypatch):
    """test_pick_agent_raises_on_unrecognized_agent_value above calls
    pick_agent() directly and only confirms the function itself raises. This
    test drives the same failure through the *real* /chat endpoint, to
    confirm main.py's global `Exception` handler actually catches it and
    returns the documented error contract — not an unhandled 500 with a
    leaked traceback — rather than assuming the handler applies here just
    because it applies elsewhere.

    route_node (app/orchestrator/nodes.py) calls build_step_plan via a
    `from .routing import build_step_plan` import, so the name to patch is
    app.orchestrator.nodes.build_step_plan (the binding actually used at call
    time), not app.orchestrator.routing.build_step_plan.

    A real deployed server converts this into a clean 500 via main.py's
    global `Exception` handler (ServerErrorMiddleware, which sits outside
    RequestContextMiddleware in the stack). Starlette's TestClient defaults
    to `raise_server_exceptions=True`, which re-raises the original
    exception into the test process instead of only returning the 500
    response — useful for catching bugs, but it would make this specific
    test (which wants to confirm the *response* the handler produces) fail
    even though the handler is working correctly. A dedicated client with
    that flag off is used here so the assertions exercise the actual HTTP
    contract a real client would see.
    """
    non_raising_client = TestClient(app, raise_server_exceptions=False)

    def _bad_step_plan(message):
        return [
            {
                "key": "step-1",
                "agent": "mystery_agent",
                "instruction": message,
                "sequence": 1,
                "status": "pending",
            }
        ]

    monkeypatch.setattr("app.orchestrator.nodes.build_step_plan", _bad_step_plan)

    response = non_raising_client.post("/chat", json={"message": "trigger an unrecognized agent value"})

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_error"
    assert "Traceback" not in body["detail"]
    assert "mystery_agent" not in body["detail"]
    for key in ("run_id", "request_id", "session_id", "tenant_id"):
        assert key in body
