"""Typed MCP tool wrappers for the private DB-agent graph.

Every DB-facing MCP call goes through the repository's shared retry helper so
retry policy and tool telemetry remain centralized. Raw tool responses are
validated immediately and returned as DB-agent models.

This module does not mutate LangGraph state, increment call sequences, choose
routes, or translate failures into terminal agent results. Those
responsibilities belong to nodes.py and routing.py.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.context import RequestContext
from app.mcp_client import call_tool
from app.retry import ToolCallable, call_tool_with_retry

from .models import (
    DocumentSearchResult,
    QueryResult,
    SchemaResult,
)
from .validation import (
    validate_document_result,
    validate_query_result,
    validate_schema_result,
)


GRAPH_NODE = "db_agent"
MAX_QUERY_ROWS = 20
DOCUMENT_TOP_K = 3


def _mcp_tool_callable(
    ctx: RequestContext,
) -> ToolCallable:
    """Bind request correlation metadata to the MCP transport."""

    async def _call(
        tool_name: str,
        args: dict,
    ) -> dict:
        return await call_tool(
            tool_name,
            args,
            ctx=ctx,
        )

    return _call


def _resolve_tool_callable(
    ctx: RequestContext,
    tool_callable: ToolCallable | None,
) -> ToolCallable:
    """Use an injected test callable or the real MCP transport."""

    if tool_callable is not None:
        return tool_callable

    return _mcp_tool_callable(ctx)


async def call_get_schema(
    *,
    ctx: RequestContext,
    step_sequence: int,
    call_sequence: int,
    tables: Sequence[str],
    tool_callable: ToolCallable | None = None,
) -> SchemaResult:
    """Fetch and validate schema metadata for requested tables."""

    table_names = list(
        dict.fromkeys(
            table.strip()
            for table in tables
            if table.strip()
        )
    )

    if not table_names:
        raise ValueError(
            "tables must contain at least one table name"
        )

    raw_result = await call_tool_with_retry(
        ctx,
        graph_node=GRAPH_NODE,
        step_sequence=step_sequence,
        call_sequence=call_sequence,
        tool_name="get_schema",
        args={
            "tables": table_names,
        },
        tool_callable=_resolve_tool_callable(
            ctx,
            tool_callable,
        ),
        idempotent=True,
    )

    return validate_schema_result(raw_result)


async def call_run_query(
    *,
    ctx: RequestContext,
    step_sequence: int,
    call_sequence: int,
    sql: str,
    tool_callable: ToolCallable | None = None,
) -> QueryResult:
    """Execute one validated read-only query."""

    if not isinstance(sql, str) or not sql.strip():
        raise ValueError(
            "sql must be a non-empty string"
        )

    raw_result = await call_tool_with_retry(
        ctx,
        graph_node=GRAPH_NODE,
        step_sequence=step_sequence,
        call_sequence=call_sequence,
        tool_name="run_query",
        args={
            "sql": sql,
            "max_rows": MAX_QUERY_ROWS,
        },
        tool_callable=_resolve_tool_callable(
            ctx,
            tool_callable,
        ),
        idempotent=True,
    )

    return validate_query_result(raw_result)


async def call_search_documents(
    *,
    ctx: RequestContext,
    step_sequence: int,
    call_sequence: int,
    query: str,
    tool_callable: ToolCallable | None = None,
) -> DocumentSearchResult:
    """Search documents and validate retrieval provenance."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError(
            "query must be a non-empty string"
        )

    raw_result = await call_tool_with_retry(
        ctx,
        graph_node=GRAPH_NODE,
        step_sequence=step_sequence,
        call_sequence=call_sequence,
        tool_name="search_documents",
        args={
            "query": query.strip(),
            "top_k": DOCUMENT_TOP_K,
        },
        tool_callable=_resolve_tool_callable(
            ctx,
            tool_callable,
        ),
        idempotent=True,
    )

    return validate_document_result(raw_result)