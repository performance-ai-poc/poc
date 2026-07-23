"""Typed data contracts for the read-only DB agent.

This module defines the stable value objects exchanged between the DB agent's
internal nodes, its LLM adapter, and the existing MCP tools.

The models intentionally contain no orchestration, tool-calling, retry,
logging, or SQL-safety logic. Those responsibilities belong to graph.py,
tools.py, nodes.py, and validation.py respectively.

Supported DB-agent operations are intentionally limited to:

- SQL-backed read operations through the MCP ``run_query`` tool.
- Document retrieval through the MCP ``search_documents`` tool.

Database writes are not represented because the repository's MCP database
tools are read-only and the demo scenario does not require mutations.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base class for DB-agent contracts with strict top-level fields.

    Unknown fields are rejected instead of silently ignored. This catches
    contract drift between the DB agent, the shared MCP client, and the MCP
    server early during unit and integration testing.
    """

    model_config = ConfigDict(extra="forbid")


class DBOperation(str, Enum):
    """Read-only execution paths supported by the DB agent."""

    SQL_FETCH = "sql_fetch"
    DOCUMENT_SEARCH = "document_search"


class SQLPlan(StrictModel):
    """Structured SQL-generation result returned by the DB LLM adapter.

    This model validates only the output shape. SELECT-only enforcement,
    statement-count checks, and other SQL guardrails belong in validation.py.
    """

    sql: str = Field(
        min_length=1,
        description="Candidate SQL statement generated for a read-only query.",
    )


class SchemaColumn(StrictModel):
    """Metadata for one database column returned by the MCP get_schema tool."""

    column: str = Field(
        min_length=1,
        description="Database column name.",
    )
    data_type: str = Field(
        min_length=1,
        description="PostgreSQL data type reported by information_schema.",
    )
    is_nullable: Literal["YES", "NO"] = Field(
        description="Whether the database column accepts NULL values.",
    )
    position: int = Field(
        ge=1,
        description="One-based ordinal position of the column in its table.",
    )


class SchemaResult(StrictModel):
    """Validated response returned by the MCP get_schema tool.

    Each dictionary key is a requested table name. A requested table that does
    not exist may still be present with an empty column list, matching the
    current MCP server contract.
    """

    tables: dict[str, list[SchemaColumn]] = Field(
        min_length=1,
        description="Mapping of table names to ordered column metadata.",
    )


class QueryResult(StrictModel):
    """Validated response returned by the MCP run_query tool."""

    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="JSON-safe rows returned by the read-only query.",
    )
    row_count: int = Field(
        ge=0,
        description="Number of rows included in rows.",
    )
    exec_ms: float = Field(
        ge=0,
        description="Database execution time reported by the MCP server.",
    )

    @model_validator(mode="after")
    def validate_row_count(self) -> Self:
        """Ensure row_count describes the rows actually returned."""

        if self.row_count != len(self.rows):
            raise ValueError(
                "row_count must equal the number of rows returned"
            )
        return self


class DocumentChunk(StrictModel):
    """One ranked document chunk returned by search_documents."""

    id: str = Field(
        min_length=1,
        description="Stable opaque retrieval identifier for the document chunk.",
    )
    text: str = Field(
        min_length=1,
        description="Retrieved document text used by the agent for summarization.",
    )
    score: float = Field(
        ge=0,
        description="Non-negative relevance score reported by PostgreSQL search.",
    )


class DocumentSearchResult(StrictModel):
    """Validated response returned by the MCP search_documents tool."""

    results: list[DocumentChunk] = Field(
        default_factory=list,
        description="Ranked document chunks returned by the search.",
    )
    retrieval_ids: list[str] = Field(
        default_factory=list,
        description="Stable chunk IDs propagated as retrieval provenance.",
    )
    count: int = Field(
        ge=0,
        description="Number of document chunks returned.",
    )

    @model_validator(mode="after")
    def validate_result_metadata(self) -> Self:
        """Ensure count and retrieval_ids match the returned chunks."""

        if self.count != len(self.results):
            raise ValueError(
                "count must equal the number of document results"
            )

        expected_ids = [result.id for result in self.results]
        if self.retrieval_ids != expected_ids:
            raise ValueError(
                "retrieval_ids must match result IDs in result order"
            )

        return self


class DBAgentResult(StrictModel):
    """Stable step result returned from the DB subgraph to the orchestrator.

    The outer db_agent_node serializes this model and stores it under
    ``RunState["step_results"][step_key]``. The parent advance node relies on
    status, summary, duration_ms, and error; the remaining fields implement the
    DB-agent output contract from the architecture specification.
    """

    status: Literal["success", "error"] = Field(
        description="Terminal outcome of the DB-agent step.",
    )
    sql_executed: str | None = Field(
        default=None,
        description="Validated SQL sent to run_query, or None for document search.",
    )
    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Rows returned by a successful SQL-fetch operation.",
    )
    row_count: int = Field(
        default=0,
        ge=0,
        description="Number of rows included in rows.",
    )
    retrieval_ids: list[str] = Field(
        default_factory=list,
        description="Stable document chunk IDs returned by document search.",
    )
    summary: str = Field(
        default="",
        max_length=2_000,
        description="Short natural-language summary consumed by assemble_node.",
    )
    duration_ms: int = Field(
        ge=0,
        description="Total DB-agent step duration in milliseconds.",
    )
    error: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Sanitized error category for failed steps.",
    )

    @model_validator(mode="after")
    def validate_terminal_result(self) -> Self:
        """Enforce consistency across the terminal result fields."""

        if self.row_count != len(self.rows):
            raise ValueError(
                "row_count must equal the number of rows returned"
            )

        if self.status == "success":
            if self.error is not None:
                raise ValueError(
                    "successful DB-agent results cannot contain an error"
                )
            if not self.summary.strip():
                raise ValueError(
                    "successful DB-agent results require a summary"
                )
        elif self.error is None:
            raise ValueError(
                "failed DB-agent results require an error category"
            )

        return self
