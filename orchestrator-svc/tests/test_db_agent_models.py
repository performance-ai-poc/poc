"""Unit tests for the DB agent's strict data contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.db.models import (
    DBAgentResult,
    DBOperation,
    DocumentSearchResult,
    QueryResult,
    SQLPlan,
    SchemaResult,
)


def test_db_operation_contains_only_supported_read_paths() -> None:
    """The demo DB agent must expose no database-write operation."""

    assert {operation.value for operation in DBOperation} == {
        "sql_fetch",
        "document_search",
    }


def test_sql_plan_accepts_one_structured_sql_field() -> None:
    """SQLPlan should preserve the candidate SQL for later validation."""

    plan = SQLPlan(sql="SELECT id FROM orders LIMIT 20")

    assert plan.sql == "SELECT id FROM orders LIMIT 20"


def test_sql_plan_rejects_unknown_fields() -> None:
    """Unexpected LLM output fields should fail instead of being ignored."""

    with pytest.raises(ValidationError):
        SQLPlan.model_validate(
            {
                "sql": "SELECT id FROM orders",
                "operation": "update",
            }
        )


def test_schema_result_matches_get_schema_contract() -> None:
    """Schema metadata should parse using the current MCP response shape."""

    result = SchemaResult.model_validate(
        {
            "tables": {
                "orders": [
                    {
                        "column": "id",
                        "data_type": "integer",
                        "is_nullable": "NO",
                        "position": 1,
                    },
                    {
                        "column": "status",
                        "data_type": "text",
                        "is_nullable": "NO",
                        "position": 2,
                    },
                ],
                "shipments": [],
            }
        }
    )

    assert result.tables["orders"][0].column == "id"
    assert result.tables["orders"][1].position == 2
    assert result.tables["shipments"] == []


def test_schema_result_rejects_invalid_column_position() -> None:
    """Column positions from information_schema must be one-based."""

    with pytest.raises(ValidationError):
        SchemaResult.model_validate(
            {
                "tables": {
                    "orders": [
                        {
                            "column": "id",
                            "data_type": "integer",
                            "is_nullable": "NO",
                            "position": 0,
                        }
                    ]
                }
            }
        )


def test_query_result_matches_run_query_contract() -> None:
    """QueryResult should accept rows, row_count, and execution latency."""

    result = QueryResult.model_validate(
        {
            "rows": [
                {"id": 101, "status": "failed"},
                {"id": 102, "status": "failed"},
            ],
            "row_count": 2,
            "exec_ms": 4.25,
        }
    )

    assert result.row_count == 2
    assert result.rows[0]["id"] == 101
    assert result.exec_ms == 4.25


def test_query_result_rejects_mismatched_row_count() -> None:
    """Malformed MCP query metadata must fail validation."""

    with pytest.raises(
        ValidationError,
        match="row_count must equal",
    ):
        QueryResult.model_validate(
            {
                "rows": [{"id": 101}],
                "row_count": 2,
                "exec_ms": 1.0,
            }
        )


def test_document_search_result_matches_mcp_contract() -> None:
    """Document results should preserve stable retrieval IDs and ranking."""

    result = DocumentSearchResult.model_validate(
        {
            "results": [
                {
                    "id": "doc_042#chunk_3",
                    "text": "Escalate failed orders to operations.",
                    "score": 0.91,
                },
                {
                    "id": "doc_051#chunk_1",
                    "text": "Notify the carrier before escalation.",
                    "score": 0.74,
                },
            ],
            "retrieval_ids": [
                "doc_042#chunk_3",
                "doc_051#chunk_1",
            ],
            "count": 2,
        }
    )

    assert result.count == 2
    assert result.retrieval_ids[0] == "doc_042#chunk_3"


def test_document_search_result_rejects_mismatched_ids() -> None:
    """retrieval_ids must describe the returned chunks in the same order."""

    with pytest.raises(
        ValidationError,
        match="retrieval_ids must match",
    ):
        DocumentSearchResult.model_validate(
            {
                "results": [
                    {
                        "id": "doc_042#chunk_3",
                        "text": "Escalation policy.",
                        "score": 0.91,
                    }
                ],
                "retrieval_ids": ["wrong-id"],
                "count": 1,
            }
        )


def test_successful_db_agent_result_matches_parent_contract() -> None:
    """A successful SQL result should be ready for step_results storage."""

    result = DBAgentResult(
        status="success",
        sql_executed="SELECT id FROM orders LIMIT 20",
        rows=[{"id": 101}],
        row_count=1,
        retrieval_ids=[],
        summary="Found one matching order.",
        duration_ms=125,
        error=None,
    )

    dumped = result.model_dump()

    assert dumped["status"] == "success"
    assert dumped["summary"] == "Found one matching order."
    assert dumped["duration_ms"] == 125
    assert dumped["error"] is None


def test_failed_db_agent_result_requires_error_category() -> None:
    """A failed step must expose a sanitized category to advance_node."""

    with pytest.raises(
        ValidationError,
        match="require an error category",
    ):
        DBAgentResult(
            status="error",
            summary="",
            duration_ms=20,
            error=None,
        )


def test_successful_db_agent_result_requires_summary() -> None:
    """assemble_node requires a non-empty summary for successful steps."""

    with pytest.raises(
        ValidationError,
        match="require a summary",
    ):
        DBAgentResult(
            status="success",
            summary="   ",
            duration_ms=20,
        )


def test_failed_db_agent_result_accepts_stable_empty_data_shape() -> None:
    """Failures should retain the same predictable output fields as successes."""

    result = DBAgentResult(
        status="error",
        sql_executed=None,
        rows=[],
        row_count=0,
        retrieval_ids=[],
        summary="",
        duration_ms=20,
        error="tool_error",
    )

    assert result.model_dump() == {
        "status": "error",
        "sql_executed": None,
        "rows": [],
        "row_count": 0,
        "retrieval_ids": [],
        "summary": "",
        "duration_ms": 20,
        "error": "tool_error",
    }
