"""Pure branch-selection logic for the private DB-agent graph.

Routing functions return symbolic decisions for the immediate next transition.
They do not return complete execution paths and do not know which concrete
LangGraph node implements each decision.

For example:

    route_after_classification(state) -> "schema_required"

The graph later maps that decision to a node:

    "schema_required" -> "fetch_schema"

This module performs no state mutation, LLM calls, MCP calls, validation,
logging, retrying, or result construction.
"""

from __future__ import annotations

import re
from typing import Literal

from .models import DBOperation
from .state import DBAgentState


MAX_SQL_CORRECTIONS = 1


ClassificationRoute = Literal[
    "schema_required",
    "sql_generation_ready",
    "document_retrieval",
    "terminal_failure",
]

SchemaRoute = Literal[
    "sql_generation_ready",
    "terminal_failure",
]

SQLValidationRoute = Literal[
    "sql_execution",
    "sql_correction",
    "terminal_failure",
]

QueryExecutionRoute = Literal[
    "query_summary",
    "sql_correction",
    "terminal_failure",
]

DocumentExecutionRoute = Literal[
    "document_summary",
    "terminal_failure",
]


_DOCUMENT_TERMS = (
    "document",
    "documents",
    "policy",
    "policies",
    "procedure",
    "procedures",
    "guideline",
    "guidelines",
    "handbook",
    "manual",
    "playbook",
)

_DOCUMENT_PATTERNS = tuple(
    re.compile(
        rf"\b{re.escape(term)}\b",
        flags=re.IGNORECASE,
    )
    for term in _DOCUMENT_TERMS
)

_PARENT_SQL_DIRECTIVE = re.compile(
    r"^\s*Look up matching records in the database\.\s*",
    flags=re.IGNORECASE,
)

_PARENT_DOCUMENT_DIRECTIVE = re.compile(
    r"^\s*Search documents for the relevant policy\.\s*",
    flags=re.IGNORECASE,
)

_ORIGINAL_REQUEST_MARKER = re.compile(
    r"\bOriginal request:\s*",
    flags=re.IGNORECASE,
)


def _classification_seed(instruction: str) -> str:
    """Use only the parent directive when the instruction contains one.

    The appended original request may mention other DB, API, or document terms.
    Routing should follow the explicit parent step directive first, not the
    carried-through free-text request.
    """

    normalized_instruction = instruction.strip()

    match = _ORIGINAL_REQUEST_MARKER.search(
        normalized_instruction
    )
    if match is not None:
        return normalized_instruction[: match.start()].strip()

    return normalized_instruction


def classify_operation(
    instruction: str,
) -> DBOperation:
    """Classify an instruction as relational or document retrieval.

    Explicit parent-orchestrator directives are checked first. This prevents
    words belonging to another step in the appended original request from
    changing the current step's intended operation.

    Standalone DB-agent instructions without a parent directive continue to
    use document-keyword classification, with SQL retrieval as the default.
    """

    directive_text = _classification_seed(instruction)

    if _PARENT_SQL_DIRECTIVE.search(directive_text):
        return DBOperation.SQL_FETCH

    if _PARENT_DOCUMENT_DIRECTIVE.search(directive_text):
        return DBOperation.DOCUMENT_SEARCH

    if any(
        pattern.search(directive_text)
        for pattern in _DOCUMENT_PATTERNS
    ):
        return DBOperation.DOCUMENT_SEARCH

    return DBOperation.SQL_FETCH


def route_after_classification(
    state: DBAgentState,
) -> ClassificationRoute:
    """Return the immediate decision after operation classification."""

    if _has_terminal_failure(state):
        return "terminal_failure"

    operation = state["operation"]

    if operation == DBOperation.DOCUMENT_SEARCH:
        return "document_retrieval"

    if operation == DBOperation.SQL_FETCH:
        if state["schema"] is None:
            return "schema_required"

        return "sql_generation_ready"

    return "terminal_failure"


def route_after_schema_fetch(
    state: DBAgentState,
) -> SchemaRoute:
    """Return the immediate decision after schema retrieval.

    The schema-fetch node is responsible for marking structurally valid but
    unusable responses, such as an empty required schema, as terminal errors.
    Routing only checks whether a usable schema was placed in state.
    """

    if _has_terminal_failure(state):
        return "terminal_failure"

    if state["schema"] is None:
        return "terminal_failure"

    return "sql_generation_ready"


def route_after_sql_validation(
    state: DBAgentState,
) -> SQLValidationRoute:
    """Choose execution, one correction attempt, or terminal failure."""

    if _has_terminal_failure(state):
        return "terminal_failure"

    if state["sql_plan"] is None:
        return "terminal_failure"

    if not state["validation_errors"]:
        return "sql_execution"

    if state["correction_count"] < MAX_SQL_CORRECTIONS:
        return "sql_correction"

    return "terminal_failure"


def route_after_query_execution(
    state: DBAgentState,
) -> QueryExecutionRoute:
    """Choose query summarization, SQL correction, or failure.

    The query-execution node must follow this contract:

    - On success, set ``query_result``.
    - On a correctable SQL/database error, set ``last_error``.
    - On a transport, retry-exhaustion, or response-contract failure, set
      ``error`` and mark the state as an error.

    Only errors explicitly represented through ``last_error`` are eligible for
    the single SQL-correction pass.
    """

    if _has_terminal_failure(state):
        return "terminal_failure"

    if state["query_result"] is not None:
        return "query_summary"

    if (
        state["sql_plan"] is not None
        and state["last_error"] is not None
        and state["correction_count"] < MAX_SQL_CORRECTIONS
    ):
        return "sql_correction"

    return "terminal_failure"


def route_after_document_search(
    state: DBAgentState,
) -> DocumentExecutionRoute:
    """Choose document summarization or terminal failure."""

    if _has_terminal_failure(state):
        return "terminal_failure"

    if state["document_result"] is not None:
        return "document_summary"

    return "terminal_failure"


def _has_terminal_failure(
    state: DBAgentState,
) -> bool:
    """Return whether the invocation already contains a terminal error."""

    return state["status"] == "error" or state["error"] is not None