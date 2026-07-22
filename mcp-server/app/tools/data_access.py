"""psycopg connection sources — two deliberately separate paths.

**Read-only** (:func:`get_ro_pool` / :func:`execute_readonly`): a pooled
connection to a SELECT-only role. Every statement runs inside a ``READ ONLY``
transaction with a ``statement_timeout``. This is what the tools use
(``run_query`` / ``search_documents`` / ``get_schema``) and it is the
SELECT-only enforcement *by the database* — belt-and-suspenders with the
read-only role's grants. A write attempt raises a psycopg error the tool layer
maps to a (non-retryable) ``ToolError``.

**Read-write** (:func:`rw_connection`): a plain connection to the owner role,
used **only** by seeding (``app/seed/build_seed.py``). It is never imported by
any tool, so there is no code path from a tool to a writable connection.
"""

from __future__ import annotations

import time
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

_ro_pool: ConnectionPool | None = None


def get_ro_pool() -> ConnectionPool:
    """Lazily create and open the read-only connection pool (process singleton)."""
    global _ro_pool
    if _ro_pool is None:
        _ro_pool = ConnectionPool(
            conninfo=settings.readonly_database_url,
            min_size=1,
            max_size=5,
            open=True,
            kwargs={"row_factory": dict_row, "autocommit": False},
        )
    return _ro_pool


def close_ro_pool() -> None:
    global _ro_pool
    if _ro_pool is not None:
        _ro_pool.close()
        _ro_pool = None


def _json_safe(value: Any) -> Any:
    """Coerce a DB value into something ``json.dumps`` can serialize.

    Only touches the types Postgres commonly hands back that json can't encode
    (datetimes, Decimal, etc.). Kept here so *rows never carry non-serializable
    values out of this module* — the tool return dicts must be JSON-clean.
    """
    import datetime as _dt
    import decimal as _decimal

    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _decimal.Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


def execute_readonly(
    sql: str,
    params: Any = None,
    *,
    max_rows: int | None = 20,
    statement_timeout_ms: int | None = None,
) -> dict[str, Any]:
    """Run one statement in a ``READ ONLY`` transaction and return JSON-safe rows.

    Returns ``{"rows": list[dict], "row_count": int, "exec_ms": float}``. Enforces
    read-only-ness two ways: the connection is put in read-only mode (so any
    write raises ``psycopg.errors.ReadOnlySqlTransaction``) *and* the DSN points
    at a SELECT-only role (so a write also hits ``InsufficientPrivilege``). The
    caller (db_tools) maps those to ``ToolError``.

    ``exec_ms`` measures just the ``cur.execute(sql)`` call, so it reflects
    query time rather than pool checkout / fetch overhead.
    """
    timeout = statement_timeout_ms if statement_timeout_ms is not None else settings.statement_timeout_ms
    pool = get_ro_pool()
    with pool.connection() as conn:
        # Set before any statement opens a transaction. Combined with the
        # read-only role this makes SELECT-only enforcement come from the DB.
        conn.read_only = True
        with conn.cursor() as cur:
            # SET LOCAL scopes the timeout to this transaction only.
            cur.execute(f"SET LOCAL statement_timeout = {int(timeout)}")
            start = time.perf_counter()
            cur.execute(sql, params)
            exec_ms = round((time.perf_counter() - start) * 1000, 2)
            if cur.description is None:
                # Not a row-returning statement (shouldn't happen for our tools).
                return {"rows": [], "row_count": 0, "exec_ms": exec_ms}
            raw = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
            rows = [{k: _json_safe(v) for k, v in r.items()} for r in raw]
            return {"rows": rows, "row_count": len(rows), "exec_ms": exec_ms}


def rw_connection() -> psycopg.Connection:
    """A read-write connection for seeding ONLY. Never used by a tool."""
    return psycopg.connect(settings.database_url, autocommit=False, row_factory=dict_row)
