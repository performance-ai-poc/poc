"""DB Agent.

Simple in-process DB executor sub-agent.

Flow:
1. Read the active parent step from RunState.
2. Classify the step as SQL fetch or document search.
3. Fetch schema (SQL path) or search documents (document path).
4. Generate SQL, validate it, and execute it through the shared retry helper.
5. Summarize the validated result.
6. Store a DBAgentResult under step_results[step["key"]].

Live mode uses the shared MCP server. Offline mode uses deterministic
stand-ins so the repo can still run end-to-end while the MCP client work is
being completed elsewhere.
"""

from __future__ import annotations

from app.telemetry import trace_agent_step

# TEST-ONLY, not demo/production behavior: with no real DB Agent or MCP
# server to inject a genuine failure into yet, this trigger phrase lets
# tests deliberately force this stub to fail so the orchestrator's
# abort-on-first-failure path (app/orchestrator/nodes.py) can be exercised
# end-to-end. Checked against the raw message (not the canned per-rule
# instruction text) so a single /chat call can still produce a multi-step
# plan while forcing this specific step to fail. Never triggered by demo
# traffic — the trigger string is not an ordinary English phrase.
import re
import time
from typing import Any

from app.config import settings
from app.context import RequestContext
from app.logging_utils import log_agent_llm_call
from app.orchestrator.state import RunState
from app.retry import ToolError

from .db.llm_adapter import (
    DBLLMError,
    LLMMeta,
    correct_sql,
    generate_sql,
    summarize_document_result,
    summarize_query_result,
)
from .db.models import DBAgentResult, DBOperation, SchemaResult
from .db.routing import classify_operation
from .db.tools import call_get_schema, call_run_query, call_search_documents
from .db.validation import DBToolResultValidationError, normalize_sql, validate_select_sql


GRAPH_NODE = "db_agent"
FORCE_FAILURE_TRIGGER = "__FORCE_DB_AGENT_FAILURE__"

RELATIONAL_TABLES = ("orders", "customers", "shipments")

OFFLINE_SCHEMA: dict[str, list[dict[str, Any]]] = {
    "orders": [
        {
            "column": "id",
            "data_type": "integer",
            "is_nullable": "NO",
            "position": 1,
        },
        {
            "column": "customer_id",
            "data_type": "integer",
            "is_nullable": "NO",
            "position": 2,
        },
        {
            "column": "status",
            "data_type": "text",
            "is_nullable": "NO",
            "position": 3,
        },
        {
            "column": "total_cents",
            "data_type": "integer",
            "is_nullable": "NO",
            "position": 4,
        },
        {
            "column": "created_at",
            "data_type": "timestamp with time zone",
            "is_nullable": "NO",
            "position": 5,
        },
    ],
    "customers": [
        {
            "column": "id",
            "data_type": "integer",
            "is_nullable": "NO",
            "position": 1,
        },
        {
            "column": "name",
            "data_type": "text",
            "is_nullable": "NO",
            "position": 2,
        },
        {
            "column": "region",
            "data_type": "text",
            "is_nullable": "NO",
            "position": 3,
        },
        {
            "column": "created_at",
            "data_type": "timestamp with time zone",
            "is_nullable": "NO",
            "position": 4,
        },
    ],
    "shipments": [
        {
            "column": "id",
            "data_type": "integer",
            "is_nullable": "NO",
            "position": 1,
        },
        {
            "column": "order_id",
            "data_type": "integer",
            "is_nullable": "NO",
            "position": 2,
        },
        {
            "column": "carrier",
            "data_type": "text",
            "is_nullable": "NO",
            "position": 3,
        },
        {
            "column": "tracking_number",
            "data_type": "text",
            "is_nullable": "NO",
            "position": 4,
        },
        {
            "column": "status",
            "data_type": "text",
            "is_nullable": "NO",
            "position": 5,
        },
        {
            "column": "last_update",
            "data_type": "timestamp with time zone",
            "is_nullable": "NO",
            "position": 6,
        },
    ],
}

OFFLINE_ORDERS = [
    {
        "id": 1001,
        "customer_id": 1,
        "status": "failed",
        "total_cents": 12999,
        "created_at": "2026-07-21T10:00:00Z",
    },
    {
        "id": 1002,
        "customer_id": 2,
        "status": "failed",
        "total_cents": 4500,
        "created_at": "2026-07-19T09:15:00Z",
    },
    {
        "id": 1003,
        "customer_id": 3,
        "status": "failed",
        "total_cents": 78900,
        "created_at": "2026-07-18T14:40:00Z",
    },
    {
        "id": 1009,
        "customer_id": 4,
        "status": "failed",
        "total_cents": 9900,
        "created_at": "2026-06-23T11:30:00Z",
    },
]

OFFLINE_CUSTOMERS = [
    {
        "id": 1,
        "name": "Acme Robotics",
        "region": "us-west",
        "created_at": "2025-06-10T08:00:00Z",
    },
    {
        "id": 2,
        "name": "Globex Logistics",
        "region": "us-east",
        "created_at": "2025-05-22T12:30:00Z",
    },
    {
        "id": 3,
        "name": "Initech Retail",
        "region": "eu-central",
        "created_at": "2025-04-18T16:20:00Z",
    },
]

OFFLINE_SHIPMENTS = [
    {
        "id": 2001,
        "order_id": 1001,
        "carrier": "UPS",
        "tracking_number": "1Z-ACME-0001",
        "status": "exception",
        "last_update": "2026-07-22T08:00:00Z",
    },
    {
        "id": 2002,
        "order_id": 1002,
        "carrier": "FedEx",
        "tracking_number": "FX-GLOBEX-0002",
        "status": "lost",
        "last_update": "2026-07-20T09:30:00Z",
    },
    {
        "id": 2003,
        "order_id": 1003,
        "carrier": "DHL",
        "tracking_number": "DHL-INITECH-0003",
        "status": "delayed",
        "last_update": "2026-07-19T13:15:00Z",
    },
]

OFFLINE_DOCUMENT_CHUNKS = [
    {
        "id": "doc_007#chunk_1",
        "text": (
            "Failed orders that remain unresolved for 48 hours must be "
            "escalated to the operations manager."
        ),
        "score": 0.95,
    }
]


class DBAgentError(Exception):
    """Sanitized internal DB-agent failure category."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _tool_callable(ctx: RequestContext):
    """Return live MCP transport or deterministic offline transport."""

    if settings.agent_live_calls:
        from app.mcp_client import call_tool

        async def _call(tool_name: str, args: dict) -> dict:
            return await call_tool(tool_name, args, ctx=ctx)

        return _call

    async def _call(tool_name: str, args: dict) -> dict:
        return await _offline_tool(tool_name, args)

    return _call


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


async def db_agent_node(state: RunState) -> RunState:
    """Execute the active parent step through the DB agent."""

@trace_agent_step
def db_agent_node(state):
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
    tool_callable = _tool_callable(ctx)

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
                tool_callable=tool_callable,
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
                tool_callable=tool_callable,
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
                    tool_callable=tool_callable,
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
                    tool_callable=tool_callable,
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


async def _offline_tool(tool_name: str, args: dict) -> dict:
    """Deterministic offline equivalents of the DB MCP tools."""

    if tool_name == "get_schema":
        requested = args.get("tables", RELATIONAL_TABLES)
        table_names = [
            table.strip()
            for table in requested
            if isinstance(table, str) and table.strip()
        ]
        if not table_names:
            raise DBAgentError("invalid_tables")

        return {
            "tables": {
                table: OFFLINE_SCHEMA.get(table, [])
                for table in table_names
            }
        }

    if tool_name == "run_query":
        sql = str(args.get("sql", "")).lower()
        max_rows = max(1, int(args.get("max_rows", 20)))

        if "from customers" in sql:
            rows = OFFLINE_CUSTOMERS

        elif "from shipments" in sql:
            rows = OFFLINE_SHIPMENTS

        elif "status = 'failed'" in sql and "interval '7 days'" in sql:
            rows = OFFLINE_ORDERS[:3]

        elif "status = 'failed'" in sql:
            rows = OFFLINE_ORDERS

        elif "count(*)" in sql:
            rows = [{"count": len(OFFLINE_ORDERS)}]

        else:
            rows = OFFLINE_ORDERS[:3]

        rows = [dict(row) for row in rows[:max_rows]]

        if rows and "count" in rows[0]:
            row_count = rows[0]["count"]
        else:
            row_count = len(rows)

        return {
            "rows": rows,
            "row_count": row_count,
            "exec_ms": 0.0,
        }

    if tool_name == "search_documents":
        return {
            "results": [dict(chunk) for chunk in OFFLINE_DOCUMENT_CHUNKS],
            "retrieval_ids": [chunk["id"] for chunk in OFFLINE_DOCUMENT_CHUNKS],
            "count": len(OFFLINE_DOCUMENT_CHUNKS),
        }

    raise DBAgentError("unknown_tool")