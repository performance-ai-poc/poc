"""Focused tests for DB-agent MCP wrappers."""

import asyncio

import pytest

from app.agents.db.tools import (
    call_get_schema,
    call_run_query,
    call_search_documents,
)
from app.agents.db.validation import (
    DBToolResultValidationError,
)
from app.context import RequestContext


def make_context() -> RequestContext:
    return RequestContext(
        run_id="run-1",
        request_id="request-1",
        session_id="session-1",
        tenant_id="tenant-1",
    )


def test_db_tool_wrappers():
    calls = []

    async def fake_tool(name, args):
        calls.append((name, args))

        return {
            "get_schema": {
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
            },
            "run_query": {
                "rows": [{"id": 1001}],
                "row_count": 1,
                "exec_ms": 1.2,
            },
            "search_documents": {
                "results": [
                    {
                        "id": "doc_007#chunk_1",
                        "text": "Escalate after 48 hours.",
                        "score": 0.9,
                    }
                ],
                "retrieval_ids": [
                    "doc_007#chunk_1",
                ],
                "count": 1,
            },
        }[name]

    async def run():
        ctx = make_context()

        schema = await call_get_schema(
            ctx=ctx,
            step_sequence=1,
            call_sequence=1,
            tables=["orders"],
            tool_callable=fake_tool,
        )

        query = await call_run_query(
            ctx=ctx,
            step_sequence=1,
            call_sequence=2,
            sql="SELECT id FROM orders",
            tool_callable=fake_tool,
        )

        documents = await call_search_documents(
            ctx=ctx,
            step_sequence=1,
            call_sequence=3,
            query="shipment escalation",
            tool_callable=fake_tool,
        )

        return schema, query, documents

    schema, query, documents = asyncio.run(run())

    assert schema.tables["orders"][0].column == "id"
    assert query.rows == [{"id": 1001}]
    assert documents.retrieval_ids == [
        "doc_007#chunk_1"
    ]

    assert calls == [
        (
            "get_schema",
            {"tables": ["orders"]},
        ),
        (
            "run_query",
            {
                "sql": "SELECT id FROM orders",
                "max_rows": 20,
            },
        ),
        (
            "search_documents",
            {
                "query": "shipment escalation",
                "top_k": 3,
            },
        ),
    ]


def test_invalid_tool_response_is_rejected():
    async def fake_tool(name, args):
        return {
            "rows": [{"id": 1001}],
            "row_count": 2,
            "exec_ms": 1.0,
        }

    with pytest.raises(
        DBToolResultValidationError,
        match="invalid_query_result",
    ):
        asyncio.run(
            call_run_query(
                ctx=make_context(),
                step_sequence=1,
                call_sequence=1,
                sql="SELECT id FROM orders",
                tool_callable=fake_tool,
            )
        )