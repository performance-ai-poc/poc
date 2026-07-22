"""Database-backed MCP tools: ``get_schema``, ``run_query``, ``search_documents``.

Handlers are ``async`` and hand the blocking psycopg work to a worker thread
(``asyncio.to_thread``) so a query never stalls the event loop. Each takes
``ctx: Context`` (injected by FastMCP; used by the instrumentation wrapper for
correlation IDs + logging). Return shapes follow architecture §4.6.

Error mapping (see errors.py taxonomy):
- write attempt / bad SQL / off-schema  -> ``ToolError`` (non-retryable)
- statement timeout / lost connection    -> ``RetryableToolError``
"""

import asyncio

import psycopg
from mcp.server.fastmcp import Context

from app.config import settings
from app.tools import data_access
from app.tools.errors import RetryableToolError, ToolError


async def get_schema(tables: list[str], ctx: Context = None) -> dict:
    """Return column metadata for the given tables from ``information_schema``.

    Args:
        tables: Table names to describe (public schema), e.g. ``["orders", "shipments"]``.

    Returns:
        ``{"tables": {<table>: [{"column", "data_type", "is_nullable", "position"}, ...]}}``.
        Tables that don't exist simply come back with an empty column list.
    """
    if not tables:
        raise ToolError("get_schema requires a non-empty list of table names")
    sql = (
        "SELECT table_name, column_name, data_type, is_nullable, ordinal_position "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ANY(%s) "
        "ORDER BY table_name, ordinal_position"
    )
    try:
        result = await asyncio.to_thread(
            data_access.execute_readonly, sql, (list(tables),), max_rows=None
        )
    except psycopg.OperationalError:
        raise RetryableToolError("database connection error during get_schema", code="DB_OPERATIONAL")

    schema: dict[str, list[dict]] = {t: [] for t in tables}
    for row in result["rows"]:
        schema.setdefault(row["table_name"], []).append(
            {
                "column": row["column_name"],
                "data_type": row["data_type"],
                "is_nullable": row["is_nullable"],
                "position": row["ordinal_position"],
            }
        )
    return {"tables": schema}


async def run_query(sql: str, max_rows: int = 20, ctx: Context = None) -> dict:
    """Run a **SELECT** query and return its rows. SELECT-only is enforced by the database.

    The query runs as a read-only role inside a ``READ ONLY`` transaction with a
    ``statement_timeout``. Any attempt to write (INSERT/UPDATE/DELETE/DDL) is
    rejected by Postgres and surfaced as a (non-retryable) ``ToolError`` — this
    tool never parses the SQL to decide; the database is the guard.

    Args:
        sql: A single SELECT statement.
        max_rows: Hard cap on returned rows (1..200, default 20).

    Returns:
        ``{"rows": [...], "row_count": <int>, "exec_ms": <float>}``.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ToolError("run_query requires a non-empty SQL string")
    max_rows = max(1, min(int(max_rows), 200))
    try:
        result = await asyncio.to_thread(
            data_access.execute_readonly,
            sql,
            None,
            max_rows=max_rows,
            statement_timeout_ms=settings.statement_timeout_ms,
        )
    except psycopg.errors.ReadOnlySqlTransaction:
        raise ToolError("run_query permits SELECT statements only (write rejected by read-only database)")
    except psycopg.errors.InsufficientPrivilege:
        raise ToolError("run_query permits SELECT statements only (insufficient privilege for write)")
    except psycopg.errors.QueryCanceled:
        raise RetryableToolError("run_query exceeded statement_timeout", code="STATEMENT_TIMEOUT")
    except psycopg.OperationalError:
        raise RetryableToolError("database connection error during run_query", code="DB_OPERATIONAL")
    except (
        psycopg.errors.SyntaxError,
        psycopg.errors.UndefinedTable,
        psycopg.errors.UndefinedColumn,
        psycopg.errors.UndefinedFunction,
        psycopg.errors.GroupingError,
        psycopg.ProgrammingError,
    ) as exc:
        raise ToolError(f"invalid query: {type(exc).__name__}")

    return {"rows": result["rows"], "row_count": result["row_count"], "exec_ms": result["exec_ms"]}


async def search_documents(query: str, top_k: int = 3, ctx: Context = None) -> dict:
    """Full-text search over seeded documents using Postgres ``tsvector``/``tsquery``.

    Uses ``websearch_to_tsquery`` + ``ts_rank_cd`` against a GIN-indexed
    generated ``tsv`` column. Each hit's ``id`` is a **stable, opaque chunk ID**
    (e.g. ``doc_007#chunk_1``); the flat ``retrieval_ids`` list is what the
    orchestrator threads through as provenance. The ``text`` field is returned to
    the caller but is **never logged** (the instrumentation wrapper logs only
    ``retrieval_ids`` and ``count``).

    Args:
        query: Natural-language search string.
        top_k: Max results to return (1..20, default 3).

    Returns:
        ``{"results": [{"id", "text", "score"}, ...], "retrieval_ids": [...], "count": <int>}``.
    """
    if not isinstance(query, str) or not query.strip():
        raise ToolError("search_documents requires a non-empty query string")
    top_k = max(1, min(int(top_k), 20))
    sql = (
        "SELECT chunk_id, doc_id, title, text, ts_rank_cd(tsv, q) AS score "
        "FROM documents, websearch_to_tsquery('english', %s) AS q "
        "WHERE tsv @@ q "
        "ORDER BY score DESC, chunk_id ASC "
        "LIMIT %s"
    )
    try:
        result = await asyncio.to_thread(
            data_access.execute_readonly, sql, (query, top_k), max_rows=top_k
        )
    except psycopg.OperationalError:
        raise RetryableToolError("database connection error during search_documents", code="DB_OPERATIONAL")

    results = [
        {"id": row["chunk_id"], "text": row["text"], "score": round(float(row["score"]), 6)}
        for row in result["rows"]
    ]
    retrieval_ids = [r["id"] for r in results]
    return {"results": results, "retrieval_ids": retrieval_ids, "count": len(results)}
