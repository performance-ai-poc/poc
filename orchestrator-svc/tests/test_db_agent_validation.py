"""Unit tests for deterministic DB-agent validation."""

from __future__ import annotations

import pytest

from app.agents.db.validation import (
    DBToolResultValidationError,
    MAX_SQL_LENGTH,
    normalize_sql,
    validate_document_result,
    validate_query_result,
    validate_schema_result,
    validate_select_sql,
)


def test_normalize_sql_removes_outer_whitespace_and_trailing_semicolon() -> None:
    """One harmless trailing semicolon should be removed."""

    assert normalize_sql("  SELECT id FROM orders;  ") == (
        "SELECT id FROM orders"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, status FROM orders",
        "select id from orders where status = 'failed'",
        (
            "WITH recent_orders AS ("
            "SELECT id FROM orders WHERE status = 'failed'"
            ") SELECT id FROM recent_orders"
        ),
        "SELECT id FROM orders WHERE note = 'update pending'",
        'SELECT "update" FROM orders',
        "SELECT id FROM orders WHERE note = 'value;still-one-statement'",
    ],
)
def test_validate_select_sql_accepts_supported_read_queries(sql: str) -> None:
    """Valid SELECT and WITH queries should pass the local guardrail."""

    assert validate_select_sql(sql) == []


@pytest.mark.parametrize(
    ("sql", "expected_error"),
    [
        ("", "empty_sql"),
        ("   ", "empty_sql"),
        ("INSERT INTO orders VALUES (1)", "select_only"),
        ("UPDATE orders SET status = 'done'", "select_only"),
        ("DELETE FROM orders", "select_only"),
        ("DROP TABLE orders", "select_only"),
        (
            "WITH changed AS ("
            "UPDATE orders SET status = 'done' RETURNING id"
            ") SELECT id FROM changed",
            "forbidden_keyword:update",
        ),
        (
            "SELECT id INTO failed_orders FROM orders",
            "forbidden_keyword:into",
        ),
        (
            "SELECT id FROM orders FOR UPDATE",
            "locking_clause_not_allowed",
        ),
        (
            "WITH changed AS (DELETE FROM orders RETURNING id)",
            "with_query_requires_select",
        ),
    ],
)
def test_validate_select_sql_rejects_unsafe_queries(
    sql: str,
    expected_error: str,
) -> None:
    """Write, DDL, and locking operations must not reach run_query."""

    assert expected_error in validate_select_sql(sql)


def test_validate_select_sql_rejects_multiple_statements() -> None:
    """Only one SQL statement may be sent to the MCP server."""

    errors = validate_select_sql(
        "SELECT id FROM orders; SELECT id FROM customers"
    )

    assert "multiple_statements" in errors


def test_validate_select_sql_allows_only_one_trailing_semicolon() -> None:
    """Two trailing semicolons still represent an invalid statement shape."""

    errors = validate_select_sql("SELECT id FROM orders;;")

    assert "multiple_statements" in errors


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM orders -- hidden clause",
        "SELECT id FROM /* hidden table */ orders",
    ],
)
def test_validate_select_sql_rejects_comments(sql: str) -> None:
    """Comments are rejected to keep generated SQL auditable."""

    assert "comments_not_allowed" in validate_select_sql(sql)


def test_validate_select_sql_ignores_comment_markers_inside_strings() -> None:
    """Text values containing comment markers should remain valid."""

    sql = "SELECT id FROM orders WHERE note = '-- not a comment'"

    assert validate_select_sql(sql) == []


def test_validate_select_sql_rejects_unterminated_quote() -> None:
    """Malformed quoted content should fail before MCP execution."""

    errors = validate_select_sql(
        "SELECT id FROM orders WHERE status = 'failed"
    )

    assert "unterminated_quote" in errors


def test_validate_select_sql_rejects_null_byte() -> None:
    """Null bytes must never be sent to the database tool."""

    errors = validate_select_sql("SELECT id FROM orders\x00")

    assert "null_byte_not_allowed" in errors


def test_validate_select_sql_rejects_excessive_length() -> None:
    """Generated SQL should have a bounded size."""

    sql = "SELECT '" + ("x" * MAX_SQL_LENGTH) + "'"

    assert "sql_too_long" in validate_select_sql(sql)


def test_validate_schema_result_parses_mcp_contract() -> None:
    """A valid get_schema response should become SchemaResult."""

    result = validate_schema_result(
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

    assert result.tables["orders"][0].column == "id"


def test_validate_schema_result_raises_sanitized_error() -> None:
    """Malformed schema responses should expose no raw payload."""

    with pytest.raises(
        DBToolResultValidationError,
        match="invalid_schema_result",
    ) as exc_info:
        validate_schema_result({"tables": {}})

    assert exc_info.value.category == "invalid_schema_result"
    assert str(exc_info.value) == "invalid_schema_result"


def test_validate_query_result_parses_mcp_contract() -> None:
    """A valid run_query response should become QueryResult."""

    result = validate_query_result(
        {
            "rows": [{"id": 101}, {"id": 102}],
            "row_count": 2,
            "exec_ms": 3.5,
        }
    )

    assert result.row_count == 2
    assert result.exec_ms == 3.5


def test_validate_query_result_raises_sanitized_error() -> None:
    """Malformed query responses should produce a stable category."""

    with pytest.raises(
        DBToolResultValidationError,
        match="invalid_query_result",
    ) as exc_info:
        validate_query_result(
            {
                "rows": [{"id": 101}],
                "row_count": 7,
                "exec_ms": 3.5,
            }
        )

    assert exc_info.value.category == "invalid_query_result"


def test_validate_document_result_parses_mcp_contract() -> None:
    """Valid document results should retain stable retrieval IDs."""

    result = validate_document_result(
        {
            "results": [
                {
                    "id": "doc_042#chunk_3",
                    "text": "Escalate failed orders.",
                    "score": 0.91,
                }
            ],
            "retrieval_ids": ["doc_042#chunk_3"],
            "count": 1,
        }
    )

    assert result.retrieval_ids == ["doc_042#chunk_3"]


def test_validate_document_result_raises_sanitized_error() -> None:
    """Malformed document metadata should not leak document text."""

    with pytest.raises(
        DBToolResultValidationError,
        match="invalid_document_result",
    ) as exc_info:
        validate_document_result(
            {
                "results": [
                    {
                        "id": "doc_042#chunk_3",
                        "text": "Sensitive document text.",
                        "score": 0.91,
                    }
                ],
                "retrieval_ids": ["different-id"],
                "count": 1,
            }
        )

    assert exc_info.value.category == "invalid_document_result"
    assert "Sensitive document text" not in str(exc_info.value)
