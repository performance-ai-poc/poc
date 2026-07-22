"""Seed a fresh database, idempotently, in one command.

    python -m app.seed.build_seed

Runs ``schema.sql`` (which drops + recreates every table, so re-running is
always clean and deterministic), ensures the SELECT-only read-only role exists
and is granted read access, then inserts the deterministic rows and document
chunks. Connects read-write via ``DATABASE_URL`` (owner role); the read-only
role/DSN (``READONLY_DATABASE_URL``) is what the tools use at query time.
"""

from __future__ import annotations

import sys
from pathlib import Path

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.config import settings
from app.seed import seed_data
from app.tools import data_access

_SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"


def _ensure_readonly_role(cur, db_name: str) -> None:
    """Create/refresh the SELECT-only role that backs READONLY_DATABASE_URL.

    Idempotent: creates the role if absent, otherwise just re-asserts its
    password, then (re)grants CONNECT + USAGE + SELECT and explicitly revokes
    write privileges. Run *after* the tables exist so the blanket SELECT grant
    covers them.
    """
    info = conninfo_to_dict(settings.readonly_database_url)
    user = info.get("user")
    password = info.get("password")
    if not user:
        print("[seed] READONLY_DATABASE_URL has no user; skipping read-only role setup")
        return

    # Utility statements (CREATE/ALTER ROLE, GRANT) can't take bound parameters,
    # so identifiers and the password literal are composed via psycopg.sql (which
    # quotes/escapes them safely) rather than string-formatted by hand.
    role = sql.Identifier(user)
    db = sql.Identifier(db_name)

    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,))
    exists = cur.fetchone() is not None
    if not exists:
        cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(role, sql.Literal(password)))
    elif password:
        cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(role, sql.Literal(password)))

    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, role))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(role))
    cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(role))
    # Defense in depth: make absolutely sure the read-only role can't write, even
    # if a future migration accidentally grants it.
    cur.execute(
        sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM {}").format(role)
    )
    print(f"[seed] read-only role '{user}' ensured (SELECT-only)")


def _insert_rows(cur) -> None:
    cur.executemany(
        "INSERT INTO customers (id, name, region, created_at) "
        "VALUES (%s, %s, %s, now() - make_interval(days => %s))",
        seed_data.CUSTOMERS,
    )
    cur.executemany(
        "INSERT INTO orders (id, customer_id, status, total_cents, created_at) "
        "VALUES (%s, %s, %s, %s, now() - make_interval(days => %s))",
        seed_data.ORDERS,
    )
    cur.executemany(
        "INSERT INTO shipments (id, order_id, carrier, tracking_number, status, last_update) "
        "VALUES (%s, %s, %s, %s, %s, now() - make_interval(days => %s))",
        seed_data.SHIPMENTS,
    )
    cur.executemany(
        "INSERT INTO documents (chunk_id, doc_id, title, text) VALUES (%s, %s, %s, %s)",
        seed_data.documents(),
    )


def main() -> int:
    db_name = conninfo_to_dict(settings.database_url).get("dbname", "")
    schema_sql = _SCHEMA_SQL.read_text()

    with data_access.rw_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
            _ensure_readonly_role(cur, db_name)
            _insert_rows(cur)
        conn.commit()

        with conn.cursor() as cur:
            counts = {}
            for table in ("customers", "orders", "shipments", "documents"):
                cur.execute(f"SELECT count(*) AS n FROM {table}")
                counts[table] = cur.fetchone()["n"]

    print(f"[seed] done: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
