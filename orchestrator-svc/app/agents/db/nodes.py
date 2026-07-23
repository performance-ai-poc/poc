"""Execution nodes for the private read-only DB-agent graph."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.logging_utils import log_agent_llm_call
from app.retry import ToolError

from .llm_adapter import (
    DBLLMError,
    LLMMeta,
    correct_sql,
    generate_sql,
    summarize_document_result,
    summarize_query_result,
)
from .models import DBAgentResult, SQLPlan
from .routing import classify_operation
from .state import DBAgentState
from .tools import (
    GRAPH_NODE,
    call_get_schema,
    call_run_query,
    call_search_documents,
)
from .validation import (
    DBToolResultValidationError,
    normalize_sql,
    validate_select_sql,
)


RELATIONAL_TABLES = (
    "orders",
    "customers",
    "shipments",
)

T = TypeVar("T")


def classify_node(
    state: DBAgentState,
) -> DBAgentState:
    """Choose the SQL-fetch or document-search branch."""

    state["operation"] = classify_operation(
        state["instruction"]
    )
    return state


async def fetch_schema_node(
    state: DBAgentState,
) -> DBAgentState:
    """Fetch relational schema once for this invocation."""

    schema = await _tool_call(
        state,
        lambda sequence: call_get_schema(
            ctx=state["ctx"],
            step_sequence=state["step_sequence"],
            call_sequence=sequence,
            tables=RELATIONAL_TABLES,
        ),
    )

    if schema is None:
        return state

    if not any(schema.tables.values()):
        return _fail(
            state,
            "schema_unusable",
        )

    state["schema"] = schema
    return state


async def generate_sql_node(
    state: DBAgentState,
) -> DBAgentState:
    """Generate the initial SQL candidate."""

    schema = state["schema"]

    if schema is None:
        return _fail(
            state,
            "schema_missing",
        )

    plan = await _llm_call(
        state,
        generate_sql(
            state["instruction"],
            schema,
        ),
    )

    if plan is None:
        return state

    state["sql_plan"] = plan
    state["validation_errors"] = []
    state["last_error"] = None

    return state


def validate_sql_node(
    state: DBAgentState,
) -> DBAgentState:
    """Apply the local SELECT-only guardrail."""

    plan = state["sql_plan"]

    if plan is None:
        return _fail(
            state,
            "sql_plan_missing",
        )

    errors = validate_select_sql(plan.sql)
    state["validation_errors"] = errors

    if not errors:
        state["sql_plan"] = SQLPlan(
            sql=normalize_sql(plan.sql)
        )

    return state


async def run_query_node(
    state: DBAgentState,
) -> DBAgentState:
    """Execute validated SQL.

    A permanent run_query error is treated as a potentially
    correctable SQL/database error. Timeout and retry-exhaustion
    errors remain terminal.
    """

    plan = state["sql_plan"]

    if plan is None:
        return _fail(
            state,
            "sql_plan_missing",
        )

    if state["validation_errors"]:
        return _fail(
            state,
            "sql_validation_failed",
        )

    result = await _tool_call(
        state,
        lambda sequence: call_run_query(
            ctx=state["ctx"],
            step_sequence=state["step_sequence"],
            call_sequence=sequence,
            sql=plan.sql,
        ),
        correctable_sql_error=True,
    )

    if result is None:
        return state

    state["query_result"] = result
    state["last_error"] = None

    return state


async def correct_sql_node(
    state: DBAgentState,
) -> DBAgentState:
    """Perform the single correction pass allowed by routing."""

    schema = state["schema"]
    plan = state["sql_plan"]

    if schema is None:
        return _fail(
            state,
            "schema_missing",
        )

    if plan is None:
        return _fail(
            state,
            "sql_plan_missing",
        )

    state["correction_count"] += 1

    corrected = await _llm_call(
        state,
        correct_sql(
            state["instruction"],
            schema,
            plan.sql,
            list(state["validation_errors"]),
            state["last_error"],
        ),
    )

    if corrected is None:
        return state

    state["sql_plan"] = corrected
    state["validation_errors"] = []
    state["query_result"] = None
    state["last_error"] = None

    return state


async def search_documents_node(
    state: DBAgentState,
) -> DBAgentState:
    """Search document chunks using the step instruction."""

    result = await _tool_call(
        state,
        lambda sequence: call_search_documents(
            ctx=state["ctx"],
            step_sequence=state["step_sequence"],
            call_sequence=sequence,
            query=state["instruction"],
        ),
    )

    if result is not None:
        state["document_result"] = result

    return state


async def summarize_query_node(
    state: DBAgentState,
) -> DBAgentState:
    """Summarize SQL rows and create the terminal result."""

    query_result = state["query_result"]
    plan = state["sql_plan"]

    if query_result is None:
        return _fail(
            state,
            "query_result_missing",
        )

    if plan is None:
        return _fail(
            state,
            "sql_plan_missing",
        )

    summary = await _llm_call(
        state,
        summarize_query_result(
            state["instruction"],
            query_result,
        ),
    )

    if summary is None:
        return state

    return _succeed(
        state,
        summary=summary,
        sql_executed=plan.sql,
        rows=query_result.rows,
        retrieval_ids=[],
    )


async def summarize_documents_node(
    state: DBAgentState,
) -> DBAgentState:
    """Summarize document chunks and create the terminal result."""

    document_result = state["document_result"]

    if document_result is None:
        return _fail(
            state,
            "document_result_missing",
        )

    summary = await _llm_call(
        state,
        summarize_document_result(
            state["instruction"],
            document_result,
        ),
    )

    if summary is None:
        return state

    return _succeed(
        state,
        summary=summary,
        sql_executed=None,
        rows=[],
        retrieval_ids=document_result.retrieval_ids,
    )


def finalize_error_node(
    state: DBAgentState,
) -> DBAgentState:
    """Convert current failure state into the terminal contract."""

    reason = state["error"]

    if reason is None and state["validation_errors"]:
        reason = "sql_validation_failed"

    if reason is None:
        reason = (
            state["last_error"]
            or "agent_error"
        )

    plan = state["sql_plan"]

    # Include SQL only when it passed local validation.
    # Invalid candidates were never executed.
    sql_executed = (
        plan.sql
        if (
            plan is not None
            and not state["validation_errors"]
        )
        else None
    )

    state["status"] = "error"
    state["error"] = reason
    state["result"] = DBAgentResult(
        status="error",
        sql_executed=sql_executed,
        duration_ms=_duration_ms(state),
        error=reason,
    )

    return state


async def _tool_call(
    state: DBAgentState,
    call: Callable[[int], Awaitable[T]],
    *,
    correctable_sql_error: bool = False,
) -> T | None:
    """Run one logical MCP call with a new call sequence."""

    state["call_sequence"] += 1

    try:
        return await call(
            state["call_sequence"]
        )

    except ToolError as exc:
        if (
            correctable_sql_error
            and exc.reason == "tool_error"
        ):
            state["query_result"] = None
            state["last_error"] = (
                "database_query_error"
            )
        else:
            _fail(
                state,
                exc.reason,
            )

    except DBToolResultValidationError as exc:
        _fail(
            state,
            exc.category,
        )

    except Exception:  # noqa: BLE001
        _fail(
            state,
            "agent_error",
        )

    return None


async def _llm_call(
    state: DBAgentState,
    call: Awaitable[tuple[T, LLMMeta]],
) -> T | None:
    """Run one LLM call and emit metadata-only telemetry."""

    state["call_sequence"] += 1

    try:
        value, metadata = await call

    except DBLLMError as exc:
        _fail(
            state,
            exc.reason,
        )
        return None

    except Exception:  # noqa: BLE001
        _fail(
            state,
            "agent_error",
        )
        return None

    log_agent_llm_call(
        state["ctx"],
        {
            "graph.node": GRAPH_NODE,
            "step.sequence": (
                state["step_sequence"]
            ),
            "call.sequence": (
                state["call_sequence"]
            ),
            **metadata,
        },
    )

    return value


def _succeed(
    state: DBAgentState,
    *,
    summary: str,
    sql_executed: str | None,
    rows: list[dict],
    retrieval_ids: list[str],
) -> DBAgentState:
    """Create a successful terminal result."""

    state["result"] = DBAgentResult(
        status="success",
        sql_executed=sql_executed,
        rows=rows,
        row_count=len(rows),
        retrieval_ids=retrieval_ids,
        summary=summary,
        duration_ms=_duration_ms(state),
    )
    state["status"] = "success"
    state["error"] = None

    return state


def _fail(
    state: DBAgentState,
    reason: str,
) -> DBAgentState:
    """Record a sanitized terminal error category."""

    state["status"] = "error"
    state["error"] = reason

    return state


def _duration_ms(
    state: DBAgentState,
) -> int:
    """Return non-negative elapsed agent duration."""

    elapsed = (
        time.perf_counter()
        - state["started_at"]
    )

    return max(
        0,
        int(elapsed * 1000),
    )