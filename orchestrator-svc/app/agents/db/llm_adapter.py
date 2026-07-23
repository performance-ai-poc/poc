"""LLM interface for the read-only DB agent.

The LLM generates SQL, corrects one failed SQL attempt, and summarizes DB or
document results. SQL safety is enforced separately in validation.py.

Adapted from the LLM endpoint request logic in app/llm.py.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, TypedDict

import httpx
from pydantic import ValidationError

from app.config import settings

from .models import (
    DocumentSearchResult,
    QueryResult,
    SQLPlan,
    SchemaResult,
)


class LLMMeta(TypedDict):
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class DBLLMError(Exception):
    """Sanitized LLM failure safe to store in agent state."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"DB LLM call failed: {reason}")
        self.reason = reason


_SQL_PROMPT = (
    "Generate PostgreSQL 16 read-only SQL. Return only a JSON object shaped "
    '{"sql": "..."}. Use only the supplied tables and columns. Return exactly '
    "one SELECT statement, or one WITH query ending in SELECT. Never use DML, "
    "DDL, comments, multiple statements, SELECT INTO, or locking clauses. "
    "Unless the query is an aggregate, limit results to 20 rows."
)

_CORRECTION_PROMPT = (
    "Correct the supplied PostgreSQL query using the instruction, schema, "
    "previous SQL, and sanitized errors. Return only a JSON object shaped "
    '{"sql": "..."}. Use only supplied tables and columns. Return exactly one '
    "SELECT statement, or one WITH query ending in SELECT. Never use DML, DDL, "
    "comments, multiple statements, SELECT INTO, or locking clauses. Unless "
    "the query is an aggregate, limit results to 20 rows."
)

_QUERY_SUMMARY_PROMPT = (
    "Answer the user's question using only the supplied database rows. Be "
    "clear and concise. Do not invent facts or mention agents, prompts, SQL, "
    "JSON, or internal tool calls. If there are no rows, say no matching "
    "records were found."
)

_DOCUMENT_SUMMARY_PROMPT = (
    "Answer the user's question using only the supplied document chunks. Be "
    "clear and concise. Do not invent facts or mention agents, prompts, JSON, "
    "or internal tool calls. If there are no chunks, say no relevant document "
    "content was found."
)


async def generate_sql(
    instruction: str,
    schema: SchemaResult,
) -> tuple[SQLPlan, LLMMeta]:
    """Generate a candidate read-only SQL statement."""

    _ensure_usable_schema(schema)

    if settings.agent_live_calls:
        content, meta = await _chat_completion(
            _SQL_PROMPT,
            {
                "instruction": instruction,
                "schema": schema.model_dump(mode="json"),
            },
        )
        return _parse_sql_plan(content), meta

    plan = _offline_generate_sql(
        instruction,
        schema,
    )
    return (
        plan,
        _offline_meta(
            _SQL_PROMPT,
            {
                "instruction": instruction,
                "schema": schema.model_dump(mode="json"),
            },
            plan.sql,
        ),
    )


async def correct_sql(
    instruction: str,
    schema: SchemaResult,
    previous_sql: str,
    validation_errors: list[str],
    database_error: str | None = None,
) -> tuple[SQLPlan, LLMMeta]:
    """Generate the single allowed SQL correction attempt.

    database_error must be a sanitized category, not a raw database message.
    """

    _ensure_usable_schema(schema)

    if settings.agent_live_calls:
        content, meta = await _chat_completion(
            _CORRECTION_PROMPT,
            {
                "instruction": instruction,
                "schema": schema.model_dump(mode="json"),
                "previous_sql": previous_sql,
                "validation_errors": validation_errors,
                "database_error": database_error,
            },
        )
        return _parse_sql_plan(content), meta

    plan = _offline_correct_sql(
        instruction=instruction,
        schema=schema,
        previous_sql=previous_sql,
        validation_errors=validation_errors,
        database_error=database_error,
    )
    return (
        plan,
        _offline_meta(
            _CORRECTION_PROMPT,
            {
                "instruction": instruction,
                "schema": schema.model_dump(mode="json"),
                "previous_sql": previous_sql,
                "validation_errors": validation_errors,
                "database_error": database_error,
            },
            plan.sql,
        ),
    )


async def summarize_query_result(
    instruction: str,
    result: QueryResult,
) -> tuple[str, LLMMeta]:
    """Summarize validated query rows for the parent assembler."""

    if settings.agent_live_calls:
        content, meta = await _chat_completion(
            _QUERY_SUMMARY_PROMPT,
            {
                "instruction": instruction,
                "row_count": result.row_count,
                "rows": result.rows,
            },
        )
        return _clean_summary(content), meta

    summary = _offline_summarize_query_result(result)
    return (
        summary,
        _offline_meta(
            _QUERY_SUMMARY_PROMPT,
            {
                "instruction": instruction,
                "row_count": result.row_count,
                "rows": result.rows,
            },
            summary,
        ),
    )


async def summarize_document_result(
    instruction: str,
    result: DocumentSearchResult,
) -> tuple[str, LLMMeta]:
    """Summarize validated document-search results."""

    if settings.agent_live_calls:
        content, meta = await _chat_completion(
            _DOCUMENT_SUMMARY_PROMPT,
            {
                "instruction": instruction,
                "count": result.count,
                "documents": [
                    document.model_dump(mode="json")
                    for document in result.results
                ],
            },
        )
        return _clean_summary(content), meta

    summary = _offline_summarize_document_result(result)
    return (
        summary,
        _offline_meta(
            _DOCUMENT_SUMMARY_PROMPT,
            {
                "instruction": instruction,
                "count": result.count,
                "documents": [
                    document.model_dump(mode="json")
                    for document in result.results
                ],
            },
            summary,
        ),
    )


async def _chat_completion(
    system_prompt: str,
    payload: dict[str, Any],
) -> tuple[str, LLMMeta]:
    """Call the configured OpenAI-compatible LLM endpoint."""

    if not settings.agent_live_calls:
        raise DBLLMError("llm_disabled")

    request_body = {
        "model": settings.llm_model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": json.dumps(
                    payload,
                    default=str,
                ),
            },
        ],
        "temperature": 0,
    }

    headers = {
        "Content-Type": "application/json",
    }

    if settings.llm_api_key:
        headers["Authorization"] = (
            f"Bearer {settings.llm_api_key}"
        )

    started = time.perf_counter()

    try:
        async with httpx.AsyncClient(
            timeout=settings.llm_timeout_s,
        ) as client:
            response = await client.post(
                (
                    f"{settings.llm_base_url.rstrip('/')}"
                    "/chat/completions"
                ),
                json=request_body,
                headers=headers,
            )
            response.raise_for_status()
            response_payload = response.json()

    except httpx.HTTPStatusError as exc:
        raise DBLLMError(
            f"llm_http_{exc.response.status_code}"
        ) from exc

    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise DBLLMError("llm_unreachable") from None

    except httpx.TimeoutException:
        raise DBLLMError("llm_timeout") from None

    except httpx.RequestError:
        raise DBLLMError("llm_unreachable") from None

    except ValueError as exc:
        raise DBLLMError("llm_bad_response") from exc

    latency_ms = int(
        (time.perf_counter() - started) * 1000
    )

    try:
        content = response_payload[
            "choices"
        ][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DBLLMError("llm_bad_response") from exc

    if not isinstance(content, str) or not content.strip():
        raise DBLLMError("llm_bad_response")

    usage = response_payload.get("usage") or {}

    meta: LLMMeta = {
        "model_id": str(
            response_payload.get("model")
            or settings.llm_model
        ),
        "input_tokens": _safe_int(
            usage.get("prompt_tokens")
        ),
        "output_tokens": _safe_int(
            usage.get("completion_tokens")
        ),
        "latency_ms": latency_ms,
    }

    return content.strip(), meta


def _ensure_usable_schema(schema: SchemaResult) -> None:
    """Reject schema responses containing no usable columns."""

    if not any(schema.tables.values()):
        raise DBLLMError("schema_unusable")


def _offline_generate_sql(
    instruction: str,
    schema: SchemaResult,
) -> SQLPlan:
    """Generate deterministic read-only SQL for offline demo mode."""

    text = instruction.lower()
    table = _select_table(text, schema)
    columns = _select_columns(schema, table)
    table_sql = _identifier(table)

    where: list[str] = []
    order_by: str | None = None

    if table == "orders":
        order_id = re.search(
            r"\border(?:\s+id)?\s*#?\s*(\d+)\b",
            text,
        )
        if order_id:
            where.append(f"id = {int(order_id.group(1))}")

        for status in (
            "failed",
            "processing",
            "shipped",
            "delivered",
        ):
            if re.search(rf"\b{status}\b", text):
                where.append(f"status = '{status}'")
                break

        if any(
            term in text
            for term in (
                "last week",
                "last 7 days",
                "past week",
                "last seven days",
            )
        ):
            where.append(
                "created_at >= now() - interval '7 days'"
            )

        if "id" in columns:
            order_by = "id"
        elif "created_at" in columns:
            order_by = "created_at"

    elif table == "shipments":
        order_id = re.search(
            r"\border(?:\s+id)?\s*#?\s*(\d+)\b",
            text,
        )
        if order_id:
            where.append(f"order_id = {int(order_id.group(1))}")

        for status in (
            "exception",
            "lost",
            "delayed",
            "in_transit",
            "delivered",
        ):
            if re.search(rf"\b{status}\b", text):
                where.append(f"status = '{status}'")
                break

        if "last_update" in columns:
            order_by = "last_update"

    elif table == "customers":
        for region in (
            "us-west",
            "us-east",
            "eu-central",
        ):
            if region in text:
                where.append(f"region = '{region}'")
                break

        if "id" in columns:
            order_by = "id"

    is_count = any(
        term in text
        for term in (
            "how many",
            "count",
            "number of",
        )
    )

    if is_count:
        sql = f"SELECT COUNT(*) AS count FROM {table_sql}"
    else:
        selected = ", ".join(
            _identifier(column)
            for column in columns
        )
        sql = f"SELECT {selected} FROM {table_sql}"

    if where:
        sql += " WHERE " + " AND ".join(where)

    if order_by and not is_count:
        sql += f" ORDER BY {_identifier(order_by)}"

    if not is_count:
        sql += " LIMIT 20"

    return SQLPlan(sql=sql)


def _offline_correct_sql(
    instruction: str,
    schema: SchemaResult,
    previous_sql: str,
    validation_errors: list[str],
    database_error: str | None,
) -> SQLPlan:
    """Produce a deterministic offline correction for a bad SQL attempt."""

    if validation_errors and any(
        error.startswith("forbidden_keyword:")
        or error in {
            "select_only",
            "multiple_statements",
            "comments_not_allowed",
            "locking_clause_not_allowed",
            "empty_sql",
            "sql_too_long",
            "null_byte_not_allowed",
            "unterminated_quote",
        }
        for error in validation_errors
    ):
        return _offline_generate_sql(instruction, schema)

    if database_error == "database_query_error":
        return _offline_generate_sql(instruction, schema)

    normalized_previous = previous_sql.lower()
    if "delete" in normalized_previous or "update" in normalized_previous:
        return _offline_generate_sql(instruction, schema)

    return _offline_generate_sql(instruction, schema)


def _select_table(
    text: str,
    schema: SchemaResult,
) -> str:
    """Choose a usable table from the instruction and schema."""

    preferences = (
        ("shipment", "shipments"),
        ("customer", "customers"),
        ("order", "orders"),
    )

    for term, table in preferences:
        if term in text and schema.tables.get(table):
            return table

    for table, columns in schema.tables.items():
        if columns:
            return table

    raise DBLLMError("schema_unusable")


def _select_columns(
    schema: SchemaResult,
    table: str,
) -> list[str]:
    """Select useful columns that actually exist in the schema."""

    available = [
        column.column
        for column in schema.tables[table]
    ]

    preferred = {
        "orders": [
            "id",
            "customer_id",
            "status",
            "total_cents",
            "created_at",
        ],
        "customers": [
            "id",
            "name",
            "region",
            "created_at",
        ],
        "shipments": [
            "id",
            "order_id",
            "carrier",
            "tracking_number",
            "status",
            "last_update",
        ],
    }.get(table, available[:6])

    selected = [
        column
        for column in preferred
        if column in available
    ]

    return selected or available[:6]


def _offline_summarize_query_result(result: QueryResult) -> str:
    """Create a deterministic result summary without network calls."""

    if result.row_count == 0:
        return "No matching database records were found."

    noun = "record" if result.row_count == 1 else "records"
    summary = f"I found {result.row_count} matching {noun}."

    ids = [
        row["id"]
        for row in result.rows
        if isinstance(row, dict) and row.get("id") is not None
    ]
    if ids:
        summary += " IDs: " + ", ".join(str(value) for value in ids[:20]) + "."

    statuses = list(
        dict.fromkeys(
            str(row["status"])
            for row in result.rows
            if isinstance(row, dict) and row.get("status") is not None
        )
    )
    if statuses:
        summary += " Status: " + ", ".join(statuses[:5]) + "."

    return summary


def _offline_summarize_document_result(
    result: DocumentSearchResult,
) -> str:
    """Create a deterministic document answer without network calls."""

    if result.count == 0 or not result.results:
        return "No relevant document content was found."

    text = result.results[0].text.strip()
    if len(text) > 700:
        text = text[:697].rsplit(" ", 1)[0] + "..."

    noun = "section" if result.count == 1 else "sections"

    return f"I found {result.count} relevant document {noun}. {text}"


def _clean_summary(content: str) -> str:
    """Normalize and bound a user-facing summary."""

    summary = " ".join(content.split()).strip()

    if not summary:
        raise DBLLMError("llm_bad_response")

    return summary[:1_800]


def _offline_meta(
    system_prompt: str,
    payload: object,
    output: str,
) -> LLMMeta:
    """Build deterministic telemetry metadata for offline mode."""

    input_text = system_prompt + json.dumps(
        payload,
        default=str,
        sort_keys=True,
    )

    return {
        "model_id": settings.llm_model,
        "input_tokens": _safe_int(len(input_text) // 4),
        "output_tokens": _safe_int(len(output) // 4),
        "latency_ms": 0,
    }


def _parse_sql_plan(content: str) -> SQLPlan:
    """Parse the model response into a strict SQLPlan."""

    text = content.strip()

    if text.startswith("```"):
        text = text.strip("`").strip()

        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start < 0 or end <= start:
            raise DBLLMError("llm_bad_response") from None

        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            raise DBLLMError("llm_bad_response") from None

    try:
        return SQLPlan.model_validate(payload)
    except (ValidationError, TypeError, ValueError) as exc:
        raise DBLLMError("llm_bad_response") from exc


def _identifier(value: str) -> str:
    """Quote unusual PostgreSQL identifiers safely."""

    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        return value

    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def _safe_int(value: object) -> int:
    """Parse an optional non-negative usage counter."""

    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0