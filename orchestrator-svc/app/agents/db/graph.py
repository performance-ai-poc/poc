"""LangGraph topology for the private read-only DB agent."""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, StateGraph

from .nodes import (
    classify_node,
    correct_sql_node,
    fetch_schema_node,
    finalize_error_node,
    generate_sql_node,
    run_query_node,
    search_documents_node,
    summarize_documents_node,
    summarize_query_node,
    validate_sql_node,
)
from .routing import (
    route_after_classification,
    route_after_document_search,
    route_after_query_execution,
    route_after_schema_fetch,
    route_after_sql_validation,
)
from .state import DBAgentState


PlanGenerationRoute = Literal[
    "sql_validation",
    "terminal_failure",
]

TerminalResultRoute = Literal[
    "complete",
    "terminal_failure",
]


def _route_after_plan_generation(
    state: DBAgentState,
) -> PlanGenerationRoute:
    """Continue to validation only when an SQL plan was produced."""

    if (
        state["status"] == "error"
        or state["error"] is not None
        or state["sql_plan"] is None
    ):
        return "terminal_failure"

    return "sql_validation"


def _route_after_summary(
    state: DBAgentState,
) -> TerminalResultRoute:
    """Finish only when a successful terminal result exists."""

    if (
        state["status"] == "success"
        and state["result"] is not None
    ):
        return "complete"

    return "terminal_failure"


def build_db_agent_graph():
    """Build and compile the private DB-agent graph."""

    graph = StateGraph(DBAgentState)

    graph.add_node(
        "classify",
        classify_node,
    )
    graph.add_node(
        "fetch_schema",
        fetch_schema_node,
    )
    graph.add_node(
        "generate_sql",
        generate_sql_node,
    )
    graph.add_node(
        "validate_sql",
        validate_sql_node,
    )
    graph.add_node(
        "run_query",
        run_query_node,
    )
    graph.add_node(
        "correct_sql",
        correct_sql_node,
    )
    graph.add_node(
        "search_documents",
        search_documents_node,
    )
    graph.add_node(
        "summarize_query",
        summarize_query_node,
    )
    graph.add_node(
        "summarize_documents",
        summarize_documents_node,
    )
    graph.add_node(
        "finalize_error",
        finalize_error_node,
    )

    graph.set_entry_point("classify")

    graph.add_conditional_edges(
        "classify",
        route_after_classification,
        {
            "schema_required": "fetch_schema",
            "sql_generation_ready": "generate_sql",
            "document_retrieval": "search_documents",
            "terminal_failure": "finalize_error",
        },
    )

    graph.add_conditional_edges(
        "fetch_schema",
        route_after_schema_fetch,
        {
            "sql_generation_ready": "generate_sql",
            "terminal_failure": "finalize_error",
        },
    )

    graph.add_conditional_edges(
        "generate_sql",
        _route_after_plan_generation,
        {
            "sql_validation": "validate_sql",
            "terminal_failure": "finalize_error",
        },
    )

    graph.add_conditional_edges(
        "validate_sql",
        route_after_sql_validation,
        {
            "sql_execution": "run_query",
            "sql_correction": "correct_sql",
            "terminal_failure": "finalize_error",
        },
    )

    graph.add_conditional_edges(
        "correct_sql",
        _route_after_plan_generation,
        {
            "sql_validation": "validate_sql",
            "terminal_failure": "finalize_error",
        },
    )

    graph.add_conditional_edges(
        "run_query",
        route_after_query_execution,
        {
            "query_summary": "summarize_query",
            "sql_correction": "correct_sql",
            "terminal_failure": "finalize_error",
        },
    )

    graph.add_conditional_edges(
        "search_documents",
        route_after_document_search,
        {
            "document_summary": "summarize_documents",
            "terminal_failure": "finalize_error",
        },
    )

    graph.add_conditional_edges(
        "summarize_query",
        _route_after_summary,
        {
            "complete": END,
            "terminal_failure": "finalize_error",
        },
    )

    graph.add_conditional_edges(
        "summarize_documents",
        _route_after_summary,
        {
            "complete": END,
            "terminal_failure": "finalize_error",
        },
    )

    graph.add_edge(
        "finalize_error",
        END,
    )

    return graph.compile()


compiled_db_agent = build_db_agent_graph()