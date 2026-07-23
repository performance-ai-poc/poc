"""Unit tests for the DB sub-agent's private state."""

from __future__ import annotations

import pytest

from app.agents.db.models import SchemaResult
from app.agents.db.state import create_db_agent_state
from app.context import RequestContext


@pytest.fixture
def request_context() -> RequestContext:
    """Return a stable request context for state tests."""

    return RequestContext(
        run_id="run-test-001",
        request_id="request-test-001",
        session_id="session-test-001",
        tenant_id="tenant-test-001",
    )


def test_create_state_initializes_every_field(
    request_context: RequestContext,
) -> None:
    """The factory should create a complete graph-ready state."""

    state = create_db_agent_state(
        ctx=request_context,
        step_key="step-1",
        step_sequence=1,
        instruction="Find failed orders from last week.",
        started_at=123.45,
    )

    assert state == {
        "ctx": request_context,
        "step_key": "step-1",
        "step_sequence": 1,
        "instruction": "Find failed orders from last week.",
        "prior_step_outputs": {},
        "config": {},
        "operation": None,
        "schema": None,
        "sql_plan": None,
        "validation_errors": [],
        "query_result": None,
        "document_result": None,
        "call_sequence": 0,
        "correction_count": 0,
        "last_error": None,
        "result": None,
        "status": "running",
        "error": None,
        "started_at": 123.45,
    }


def test_create_state_preserves_exact_request_context(
    request_context: RequestContext,
) -> None:
    """The DB agent must not regenerate or replace correlation IDs."""

    state = create_db_agent_state(
        ctx=request_context,
        step_key="step-1",
        step_sequence=1,
        instruction="Find failed orders.",
    )

    assert state["ctx"] is request_context
    assert state["ctx"].run_id == "run-test-001"
    assert state["ctx"].request_id == "request-test-001"
    assert state["ctx"].session_id == "session-test-001"
    assert state["ctx"].tenant_id == "tenant-test-001"


def test_create_state_copies_parent_dictionaries(
    request_context: RequestContext,
) -> None:
    """Top-level state mutations must not replace parent-owned dictionaries."""

    prior_outputs = {
        "step-1": {
            "status": "success",
            "summary": "Found two orders.",
        }
    }
    config = {
        "model": "approved-model-alias",
        "max_retries": 2,
    }

    state = create_db_agent_state(
        ctx=request_context,
        step_key="step-2",
        step_sequence=2,
        instruction="Search the escalation policy.",
        prior_step_outputs=prior_outputs,
        config=config,
    )

    assert state["prior_step_outputs"] == prior_outputs
    assert state["prior_step_outputs"] is not prior_outputs

    assert state["config"] == config
    assert state["config"] is not config


def test_create_state_accepts_schema_hint(
    request_context: RequestContext,
) -> None:
    """An optional request-level schema cache may seed the private state."""

    schema = SchemaResult.model_validate(
        {
            "tables": {
                "orders": [
                    {
                        "column": "id",
                        "data_type": "integer",
                        "is_nullable": "NO",
                        "position": 1,
                    }
                ]
            }
        }
    )

    state = create_db_agent_state(
        ctx=request_context,
        step_key="step-2",
        step_sequence=2,
        instruction="Find matching orders.",
        schema_hint=schema,
    )

    assert state["schema"] is schema


def test_create_state_strips_boundary_whitespace(
    request_context: RequestContext,
) -> None:
    """Surrounding whitespace should not enter the internal graph."""

    state = create_db_agent_state(
        ctx=request_context,
        step_key="  step-1  ",
        step_sequence=1,
        instruction="  Find failed orders.  ",
    )

    assert state["step_key"] == "step-1"
    assert state["instruction"] == "Find failed orders."


@pytest.mark.parametrize(
    ("step_key", "step_sequence", "instruction", "error_message"),
    [
        ("", 1, "Find orders.", "step_key must not be empty"),
        ("   ", 1, "Find orders.", "step_key must not be empty"),
        ("step-1", 0, "Find orders.", "step_sequence must be greater"),
        ("step-1", -1, "Find orders.", "step_sequence must be greater"),
        ("step-1", 1, "", "instruction must not be empty"),
        ("step-1", 1, "   ", "instruction must not be empty"),
    ],
)
def test_create_state_rejects_invalid_boundary_values(
    request_context: RequestContext,
    step_key: str,
    step_sequence: int,
    instruction: str,
    error_message: str,
) -> None:
    """Invalid parent input should fail before graph execution starts."""

    with pytest.raises(ValueError, match=error_message):
        create_db_agent_state(
            ctx=request_context,
            step_key=step_key,
            step_sequence=step_sequence,
            instruction=instruction,
        )


def test_create_state_rejects_negative_start_time(
    request_context: RequestContext,
) -> None:
    """A negative monotonic timestamp would create invalid durations."""

    with pytest.raises(
        ValueError,
        match="started_at must not be negative",
    ):
        create_db_agent_state(
            ctx=request_context,
            step_key="step-1",
            step_sequence=1,
            instruction="Find failed orders.",
            started_at=-1.0,
        )


def test_create_state_generates_start_time_when_not_supplied(
    request_context: RequestContext,
) -> None:
    """Production initialization should create a positive monotonic timestamp."""

    state = create_db_agent_state(
        ctx=request_context,
        step_key="step-1",
        step_sequence=1,
        instruction="Find failed orders.",
    )

    assert state["started_at"] > 0


def test_call_and_correction_sequences_start_at_zero(
    request_context: RequestContext,
) -> None:
    """No LLM or MCP call has occurred when the graph is initialized."""

    state = create_db_agent_state(
        ctx=request_context,
        step_key="step-1",
        step_sequence=1,
        instruction="Find failed orders.",
    )

    assert state["call_sequence"] == 0
    assert state["correction_count"] == 0
