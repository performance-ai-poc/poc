"""DB Agent.

Simple in-process DB executor sub-agent.

Flow:
1. Read the active parent step from RunState.
2. Classify the step as SQL fetch or document search.
3. Fetch schema (SQL path) or search documents (document path).
4. Generate SQL, validate it, and execute it through the shared retry helper.
5. Summarize the validated result.
6. Store a DBAgentResult under step_results[step["key"]].

Both live and offline tool calls go through the shared MCP client
(app/mcp_client.py), so this agent carries no transport logic of its own.
"""

from __future__ import annotations

import time

from app.context import RequestContext
from app.logging_utils import log_agent_llm_call
from app.orchestrator.state import RunState
from app.retry import ToolError
from app.telemetry import trace_agent_step

from .db.llm_adapter import (
    DBLLMError,
    correct_sql,
    generate_sql,
    summarize_document_result,
    summarize_query_result,
)
from .db.models import DBAgentResult, DBOperation
from .db.routing import classify_operation
from .db.tools import call_get_schema, call_run_query, call_search_documents
from .db.validation import DBToolResultValidationError, normalize_sql, validate_select_sql


GRAPH_NODE = "db_agent"
FORCE_FAILURE_TRIGGER = "__FORCE_DB_AGENT_FAILURE__"

RELATIONAL_TABLES = ("orders", "customers", "shipments")


class DBAgentError(Exception):
    """Sanitized internal DB-agent failure category."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _extract_original_request(instruction: str) -> str:
    marker = "Original request:"
    index = instruction.lower().find(marker.lower())
    if index >= 0:
        tail = instruction[index + len(marker) :].strip()
        if tail:
            return tail
    return instruction.strip()


def _document_query(instruction: str) -> str:
    request = _extract_original_request(instruction)
    normalized = request.lower()

    if "escalation" in normalized:
        return "escalation policy"

    if "refund" in normalized:
        return "refund policy"

    if "shipment" in normalized and "policy" in normalized:
        return "shipment policy"

    return request


@trace_agent_step
async def db_agent_node(state: RunState) -> RunState:
    """Execute the active parent step through the DB agent."""
    step = state["steps"][state["current_step"]]

    if FORCE_FAILURE_TRIGGER in state["message"]:
        state["step_results"][step["key"]] = {
            "status": "error",
            "summary": "",
            "duration_ms": 10,
            "error": "simulated_tool_failure",
        }
        return state

    started = time.perf_counter()
    ctx: RequestContext = state["ctx"]
    instruction = step["instruction"]
    step_sequence = step["sequence"]

    call_sequence = 0
    correction_used = False
    sql_executed: str | None = None

    try:
        operation = classify_operation(instruction)

        if operation == DBOperation.DOCUMENT_SEARCH:
            call_sequence += 1
            document_result = await call_search_documents(
                ctx=ctx,
                step_sequence=step_sequence,
                call_sequence=call_sequence,
                query=_document_query(instruction),
            )

            call_sequence += 1
            summary, llm_meta = await summarize_document_result(
                instruction,
                document_result,
            )
            log_agent_llm_call(
                ctx,
                {
                    "graph.node": GRAPH_NODE,
                    "step.sequence": step_sequence,
                    "call.sequence": call_sequence,
                    **llm_meta,
                },
            )

            result = DBAgentResult(
                status="success",
                sql_executed=None,
                rows=[],
                row_count=0,
                retrieval_ids=document_result.retrieval_ids,
                summary=summary,
                duration_ms=_duration_ms(started),
            )

        else:
            call_sequence += 1
            schema = await call_get_schema(
                ctx=ctx,
                step_sequence=step_sequence,
                call_sequence=call_sequence,
                tables=RELATIONAL_TABLES,
            )

            if not any(schema.tables.values()):
                raise DBAgentError("schema_unusable")

            call_sequence += 1
            plan, llm_meta = await generate_sql(
                instruction,
                schema,
            )
            log_agent_llm_call(
                ctx,
                {
                    "graph.node": GRAPH_NODE,
                    "step.sequence": step_sequence,
                    "call.sequence": call_sequence,
                    **llm_meta,
                },
            )

            validation_errors = validate_select_sql(plan.sql)
            if validation_errors:
                if correction_used:
                    raise DBAgentError("sql_validation_failed")

                correction_used = True
                call_sequence += 1
                plan, llm_meta = await correct_sql(
                    instruction,
                    schema,
                    plan.sql,
                    validation_errors,
                    None,
                )
                log_agent_llm_call(
                    ctx,
                    {
                        "graph.node": GRAPH_NODE,
                        "step.sequence": step_sequence,
                        "call.sequence": call_sequence,
                        **llm_meta,
                    },
                )

                validation_errors = validate_select_sql(plan.sql)
                if validation_errors:
                    raise DBAgentError("sql_validation_failed")

            sql_executed = normalize_sql(plan.sql)

            try:
                call_sequence += 1
                query_result = await call_run_query(
                    ctx=ctx,
                    step_sequence=step_sequence,
                    call_sequence=call_sequence,
                    sql=sql_executed,
                )

            except ToolError as exc:
                if exc.reason != "tool_error" or correction_used:
                    raise

                correction_used = True
                call_sequence += 1
                plan, llm_meta = await correct_sql(
                    instruction,
                    schema,
                    sql_executed,
                    [],
                    "database_query_error",
                )
                log_agent_llm_call(
                    ctx,
                    {
                        "graph.node": GRAPH_NODE,
                        "step.sequence": step_sequence,
                        "call.sequence": call_sequence,
                        **llm_meta,
                    },
                )

                validation_errors = validate_select_sql(plan.sql)
                if validation_errors:
                    raise DBAgentError("sql_validation_failed")

                sql_executed = normalize_sql(plan.sql)

                call_sequence += 1
                query_result = await call_run_query(
                    ctx=ctx,
                    step_sequence=step_sequence,
                    call_sequence=call_sequence,
                    sql=sql_executed,
                )

            call_sequence += 1
            summary, llm_meta = await summarize_query_result(
                instruction,
                query_result,
            )
            log_agent_llm_call(
                ctx,
                {
                    "graph.node": GRAPH_NODE,
                    "step.sequence": step_sequence,
                    "call.sequence": call_sequence,
                    **llm_meta,
                },
            )

            result = DBAgentResult(
                status="success",
                sql_executed=sql_executed,
                rows=query_result.rows,
                row_count=query_result.row_count,
                summary=summary,
                duration_ms=_duration_ms(started),
            )

        state["step_results"][step["key"]] = result.model_dump(mode="json")

    except ToolError as exc:
        state["step_results"][step["key"]] = _error_result(
            started=started,
            reason=exc.reason,
            sql_executed=sql_executed,
        )

    except DBLLMError as exc:
        state["step_results"][step["key"]] = _error_result(
            started=started,
            reason=exc.reason,
            sql_executed=sql_executed,
        )

    except DBToolResultValidationError as exc:
        state["step_results"][step["key"]] = _error_result(
            started=started,
            reason=exc.category,
            sql_executed=sql_executed,
        )

    except DBAgentError as exc:
        state["step_results"][step["key"]] = _error_result(
            started=started,
            reason=exc.reason,
            sql_executed=sql_executed,
        )

    except Exception:  # noqa: BLE001
        state["step_results"][step["key"]] = _error_result(
            started=started,
            reason="agent_error",
            sql_executed=sql_executed,
        )

    return state


def _error_result(
    *,
    started: float,
    reason: str,
    sql_executed: str | None,
) -> dict:
    return DBAgentResult(
        status="error",
        sql_executed=sql_executed,
        duration_ms=_duration_ms(started),
        error=reason,
    ).model_dump(mode="json")
    raise DBAgentError("unknown_tool")