"""Focused tests for the DB-agent LLM adapter.

These tests bias toward the repository's pipeline contract:
- offline behavior must work without live LLM access,
- live behavior must still parse OpenAI-compatible responses,
- summaries and metadata must remain deterministic enough for orchestration.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.db import llm_adapter as adapter
from app.agents.db.models import (
    DocumentSearchResult,
    QueryResult,
    SchemaResult,
)


OFFLINE_META_KEYS = {"model_id", "input_tokens", "output_tokens", "latency_ms"}


def make_schema() -> SchemaResult:
    return SchemaResult.model_validate(
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
                        "column": "customer_id",
                        "data_type": "integer",
                        "is_nullable": "NO",
                        "position": 2,
                    },
                    {
                        "column": "status",
                        "data_type": "text",
                        "is_nullable": "NO",
                        "position": 3,
                    },
                    {
                        "column": "created_at",
                        "data_type": "timestamp with time zone",
                        "is_nullable": "NO",
                        "position": 4,
                    },
                ],
                "customers": [
                    {
                        "column": "id",
                        "data_type": "integer",
                        "is_nullable": "NO",
                        "position": 1,
                    },
                    {
                        "column": "name",
                        "data_type": "text",
                        "is_nullable": "NO",
                        "position": 2,
                    },
                    {
                        "column": "region",
                        "data_type": "text",
                        "is_nullable": "NO",
                        "position": 3,
                    },
                    {
                        "column": "created_at",
                        "data_type": "timestamp with time zone",
                        "is_nullable": "NO",
                        "position": 4,
                    },
                ],
                "shipments": [
                    {
                        "column": "id",
                        "data_type": "integer",
                        "is_nullable": "NO",
                        "position": 1,
                    },
                    {
                        "column": "order_id",
                        "data_type": "integer",
                        "is_nullable": "NO",
                        "position": 2,
                    },
                    {
                        "column": "carrier",
                        "data_type": "text",
                        "is_nullable": "NO",
                        "position": 3,
                    },
                    {
                        "column": "tracking_number",
                        "data_type": "text",
                        "is_nullable": "NO",
                        "position": 4,
                    },
                    {
                        "column": "status",
                        "data_type": "text",
                        "is_nullable": "NO",
                        "position": 5,
                    },
                    {
                        "column": "last_update",
                        "data_type": "timestamp with time zone",
                        "is_nullable": "NO",
                        "position": 6,
                    },
                ],
            }
        }
    )


@pytest.mark.parametrize(
    ("instruction", "expected_table", "expected_fragment"),
    [
        (
            "Show failed orders from the last 7 days.",
            "orders",
            "created_at >= now() - interval '7 days'",
        ),
        (
            "List customers in us-west.",
            "customers",
            "region = 'us-west'",
        ),
        (
            "Show delayed shipments.",
            "shipments",
            "status = 'delayed'",
        ),
    ],
)
def test_generate_sql_offline_uses_table_heuristics(
    monkeypatch,
    instruction: str,
    expected_table: str,
    expected_fragment: str,
):
    monkeypatch.setattr(adapter.settings, "agent_live_calls", False)

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("offline generate_sql should not call _chat_completion")

    monkeypatch.setattr(adapter, "_chat_completion", fail_if_called)

    plan, meta = asyncio.run(adapter.generate_sql(instruction, make_schema()))

    assert plan.sql.startswith("SELECT ")
    assert f"FROM {expected_table}" in plan.sql
    assert expected_fragment in plan.sql
    assert "LIMIT 20" in plan.sql
    assert meta["model_id"] == adapter.settings.llm_model
    assert meta["latency_ms"] == 0
    assert meta["input_tokens"] > 0
    assert meta["output_tokens"] > 0
    assert set(meta) == OFFLINE_META_KEYS


@pytest.mark.parametrize(
    ("instruction", "previous_sql", "validation_errors", "database_error"),
    [
        (
            "Find failed orders.",
            "DELETE FROM orders",
            ["select_only", "forbidden_keyword:delete"],
            None,
        ),
        (
            "Find failed orders.",
            "SELECT missing FROM orders",
            ["unknown_column"],
            "database_query_error",
        ),
    ],
)
def test_correct_sql_offline_regenerates_read_only_sql(
    monkeypatch,
    instruction: str,
    previous_sql: str,
    validation_errors: list[str],
    database_error: str | None,
):
    monkeypatch.setattr(adapter.settings, "agent_live_calls", False)

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("offline correct_sql should not call _chat_completion")

    monkeypatch.setattr(adapter, "_chat_completion", fail_if_called)

    plan, meta = asyncio.run(
        adapter.correct_sql(
            instruction,
            make_schema(),
            previous_sql,
            validation_errors,
            database_error,
        )
    )

    assert plan.sql.startswith("SELECT ")
    assert "DELETE" not in plan.sql.upper()
    assert "UPDATE" not in plan.sql.upper()
    assert "FROM orders" in plan.sql
    assert meta["model_id"] == adapter.settings.llm_model
    assert meta["latency_ms"] == 0
    assert set(meta) == OFFLINE_META_KEYS


def test_summarize_query_result_offline_compacts_rows(monkeypatch):
    monkeypatch.setattr(adapter.settings, "agent_live_calls", False)

    result = QueryResult.model_validate(
        {
            "rows": [
                {"id": 1001, "status": "failed"},
                {"id": 1002, "status": "failed"},
            ],
            "row_count": 2,
            "exec_ms": 1.5,
        }
    )

    summary, meta = asyncio.run(
        adapter.summarize_query_result("Show failed orders.", result)
    )

    assert summary == "I found 2 matching records. IDs: 1001, 1002. Status: failed."
    assert meta["model_id"] == adapter.settings.llm_model
    assert meta["latency_ms"] == 0
    assert meta["input_tokens"] > 0
    assert meta["output_tokens"] > 0


@pytest.mark.parametrize(
    ("count", "results", "expected"),
    [
        (
            1,
            [
                {
                    "id": "doc_007#chunk_1",
                    "text": (
                        "Failed orders that remain unresolved for 48 hours must be "
                        "escalated to the operations manager."
                    ),
                    "score": 0.95,
                }
            ],
            "I found 1 relevant document section. Failed orders that remain unresolved for 48 hours must be escalated to the operations manager.",
        ),
        (
            0,
            [],
            "No relevant document content was found.",
        ),
    ],
)
def test_summarize_document_result_offline_compacts_chunks(
    monkeypatch,
    count: int,
    results: list[dict],
    expected: str,
):
    monkeypatch.setattr(adapter.settings, "agent_live_calls", False)

    retrieval_ids = [item["id"] for item in results]
    result = DocumentSearchResult.model_validate(
        {
            "results": results,
            "retrieval_ids": retrieval_ids,
            "count": count,
        }
    )

    summary, meta = asyncio.run(
        adapter.summarize_document_result("Explain the policy.", result)
    )

    assert summary == expected
    assert meta["model_id"] == adapter.settings.llm_model
    assert meta["latency_ms"] == 0
    assert meta["input_tokens"] > 0
    assert meta["output_tokens"] > 0


def test_live_path_still_parses_llm_output(monkeypatch):
    monkeypatch.setattr(adapter.settings, "agent_live_calls", True)

    responses = iter(
        [
            '{"sql": "SELECT id, status FROM orders LIMIT 20"}',
            "Found 2 matching records.",
        ]
    )
    payloads: list[dict[str, object]] = []

    async def fake_chat_completion(system_prompt: str, payload: dict[str, object]):
        payloads.append({"prompt": system_prompt, "payload": payload})
        return next(responses), {
            "model_id": "live-test-model",
            "input_tokens": 11,
            "output_tokens": 7,
            "latency_ms": 3,
        }

    monkeypatch.setattr(adapter, "_chat_completion", fake_chat_completion)

    schema = make_schema()
    query_result = QueryResult.model_validate(
        {
            "rows": [
                {"id": 1001, "status": "failed"},
                {"id": 1002, "status": "failed"},
            ],
            "row_count": 2,
            "exec_ms": 1.5,
        }
    )

    async def run_flow():
        generated = await adapter.generate_sql("Show failed orders.", schema)
        summarized = await adapter.summarize_query_result(
            "Show failed orders.",
            query_result,
        )
        return generated, summarized

    (plan, live_meta), (summary, summary_meta) = asyncio.run(run_flow())

    assert plan.sql == "SELECT id, status FROM orders LIMIT 20"
    assert live_meta["model_id"] == "live-test-model"
    assert live_meta["latency_ms"] == 3
    assert summary == "Found 2 matching records."
    assert summary_meta["model_id"] == "live-test-model"
    assert len(payloads) == 2
    assert payloads[0]["payload"]["schema"]
    assert payloads[1]["payload"]["row_count"] == 2


def test_generate_sql_rejects_empty_schema(monkeypatch):
    monkeypatch.setattr(adapter.settings, "agent_live_calls", False)

    empty_schema = SchemaResult.model_validate({"tables": {"orders": []}})

    with pytest.raises(adapter.DBLLMError) as exc:
        asyncio.run(
            adapter.generate_sql(
                "Show failed orders.",
                empty_schema,
            )
        )

    assert exc.value.reason == "schema_unusable"