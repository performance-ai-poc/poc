"""Pipeline-first integration tests for the DB agent.

The orchestrator is treated as ground truth. The DB agent is only accepted as a
solution when it satisfies the pipeline's contracts:
- deterministic offline execution
- request/context preservation
- parent graph routing
- read-only results and summaries
- the /chat HTTP contract

These tests do not patch the DB agent's internal logic. They only keep the
repository in offline mode so the real graph, adapter, validation, and
summarization code run end to end without requiring the external MCP backend.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.agents.db_agent import db_agent_node
from app.config import settings
from app.context import RequestContext
from app.main import app
from app.orchestrator.graph import compiled_graph
from app.orchestrator.state import RunState


@pytest.fixture(autouse=True)
def _offline_mode(monkeypatch: pytest.MonkeyPatch):
    """Keep the suite on the repository's deterministic path."""

    monkeypatch.setattr(settings, "agent_live_calls", False)


def _request_context() -> RequestContext:
    """Build a stable request context for direct graph invocation."""

    return RequestContext(
        run_id="run-test",
        request_id="request-test",
        session_id="session-test",
        tenant_id="tenant-test",
    )


def _initial_run_state(message: str) -> RunState:
    """Create the same shape the orchestrator expects at /chat time."""

    return {
        "ctx": _request_context(),
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


def _db_step_state(instruction: str) -> RunState:
    """Create a single-step parent state for the DB adapter boundary."""

    return {
        "ctx": _request_context(),
        "message": instruction,
        "config": {},
        "steps": [
            {
                "key": "step-1",
                "agent": "db_agent",
                "instruction": instruction,
                "sequence": 1,
                "status": "pending",
            }
        ],
        "current_step": 0,
        "step_results": {},
        "errors": [],
        "aborted": False,
        "answer": None,
        "status": "running",
    }


def _assert_successful_sql_result(result: dict) -> None:
    """Check the DB result contract for a successful SQL path."""

    assert result["status"] == "success"
    assert result["error"] is None
    assert isinstance(result["summary"], str)
    assert result["summary"].strip()
    assert isinstance(result["duration_ms"], int)
    assert result["duration_ms"] >= 0
    assert isinstance(result["sql_executed"], str)
    assert result["sql_executed"].lower().startswith("select")
    assert isinstance(result["rows"], list)
    assert result["row_count"] == len(result["rows"])
    assert result["row_count"] > 0
    assert result["retrieval_ids"] == []


def _assert_successful_document_result(result: dict) -> None:
    """Check the DB result contract for a successful document path."""

    assert result["status"] == "success"
    assert result["error"] is None
    assert isinstance(result["summary"], str)
    assert result["summary"].strip()
    assert isinstance(result["duration_ms"], int)
    assert result["duration_ms"] >= 0
    assert result["sql_executed"] is None
    assert result["rows"] == []
    assert result["row_count"] == 0
    assert result["retrieval_ids"] == ["doc_007#chunk_1"]


def test_db_agent_node_executes_sql_path_with_read_only_output():
    """The adapter should produce a valid terminal DB step on SQL requests."""

    state = _db_step_state(
        "Look up matching records in the database. "
        "Original request: Show failed orders from the last 7 days."
    )

    final_state = asyncio.run(db_agent_node(state))
    result = final_state["step_results"]["step-1"]

    _assert_successful_sql_result(result)


def test_db_agent_node_executes_document_path_with_retrieval_ids():
    """Document requests should return retrieval provenance, not SQL rows."""

    state = _db_step_state(
        "Search documents for the relevant policy. "
        "Original request: Explain the escalation policy."
    )

    final_state = asyncio.run(db_agent_node(state))
    result = final_state["step_results"]["step-1"]

    _assert_successful_document_result(result)


def test_db_agent_node_preserves_request_context_and_state_shape():
    """The adapter should not replace the parent context or corrupt state."""

    state = _db_step_state(
        "Look up matching records in the database. "
        "Original request: Show customers in us-west."
    )

    original_ctx = state["ctx"]
    final_state = asyncio.run(db_agent_node(state))

    assert final_state["ctx"] is original_ctx
    assert final_state["ctx"].run_id == "run-test"
    assert final_state["ctx"].request_id == "request-test"
    assert final_state["ctx"].session_id == "session-test"
    assert final_state["ctx"].tenant_id == "tenant-test"

    result = final_state["step_results"]["step-1"]
    assert result["status"] == "success"
    assert result["error"] is None
    assert final_state["message"] == state["message"]


def test_compiled_graph_routes_single_sql_request_to_db_agent():
    """A straightforward DB request should take the DB branch and complete."""

    final_state = asyncio.run(
        compiled_graph.ainvoke(
            _initial_run_state("Show failed orders from the last 7 days.")
        )
    )

    assert final_state["status"] == "completed"
    assert len(final_state["steps"]) == 1
    assert final_state["steps"][0]["agent"] == "db_agent"
    assert final_state["steps"][0]["status"] == "success"

    result = final_state["step_results"][final_state["steps"][0]["key"]]
    _assert_successful_sql_result(result)


def test_compiled_graph_preserves_parent_step_intent_for_mixed_requests():
    """The full graph should keep step intent stable across mixed messages."""

    message = (
        "Show failed orders, check shipment status, "
        "and explain the escalation policy."
    )

    final_state = asyncio.run(compiled_graph.ainvoke(_initial_run_state(message)))

    assert final_state["status"] == "completed"
    assert [step["agent"] for step in final_state["steps"]] == [
        "db_agent",
        "api_agent",
        "db_agent",
    ]
    assert [step["status"] for step in final_state["steps"]] == [
        "success",
        "success",
        "success",
    ]

    first_result = final_state["step_results"][final_state["steps"][0]["key"]]
    second_result = final_state["step_results"][final_state["steps"][1]["key"]]
    third_result = final_state["step_results"][final_state["steps"][2]["key"]]

    _assert_successful_sql_result(first_result)
    assert isinstance(second_result.get("calls_made"), list)
    assert len(second_result["calls_made"]) >= 1
    _assert_successful_document_result(third_result)


def test_chat_http_contract_returns_reply_and_request_metadata():
    """The /chat boundary should expose the pipeline result and IDs."""

    with TestClient(app) as client:
        response = client.post(
            "/chat",
            json={
                "message": "Show failed orders from the last 7 days.",
                "session_id": "session-test",
                "tenant_id": "tenant-test",
            },
        )

    assert response.status_code == 200
    payload = response.json()

    assert isinstance(payload["reply"], str)
    assert payload["reply"].strip()
    assert payload["run_id"]
    assert payload["request_id"]
    assert payload["session_id"] == "session-test"
    assert payload["tenant_id"] == "tenant-test"