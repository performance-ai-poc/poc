"""Tests for the OpenObserve source client. No network: the HTTP layer is
mocked with httpx.MockTransport, so these assert query shape and fail-open
behaviour deterministically.

Run from analytics-svc/:
    python -m pytest tests/test_source.py -v
"""

from __future__ import annotations

import json

import httpx
import pytest

from app import source
from app.source import (
    SourceUnavailable,
    categorical_values,
    metric_value,
    numeric_values,
    windows,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------- parsing ---

def test_numeric_values_parses_hits_including_stringy_numbers():
    def handler(request):
        return httpx.Response(200, json={"hits": [{"value": 10}, {"value": 20}, {"value": "30"}]})

    vals = numeric_values("input_tokens", "agent.llm_call", 0, 1, client=_client(handler))
    assert vals == [10.0, 20.0, 30.0]


def test_numeric_values_skips_nulls_and_non_numbers():
    def handler(request):
        return httpx.Response(200, json={"hits": [{"value": 5}, {"value": None}, {"value": "x"}]})

    vals = numeric_values("f", "e", 0, 1, client=_client(handler))
    assert vals == [5.0]


def test_empty_hits_returns_empty_list_not_error():
    def handler(request):
        return httpx.Response(200, json={"hits": []})

    assert numeric_values("f", "e", 0, 1, client=_client(handler)) == []


def test_categorical_values_parses_labels():
    def handler(request):
        return httpx.Response(200, json={"hits": [{"label": "db_agent"}, {"label": "api_agent"}]})

    vals = categorical_values("agent", "agent.step_completed", 0, 1, client=_client(handler))
    assert vals == ["db_agent", "api_agent"]


# ------------------------------------------------------------ fail-open ---

def test_http_error_raises_source_unavailable():
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(SourceUnavailable):
        numeric_values("f", "e", 0, 1, client=_client(handler))


def test_connection_error_raises_source_unavailable():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SourceUnavailable):
        numeric_values("f", "e", 0, 1, client=_client(handler))


def test_malformed_json_raises_source_unavailable():
    def handler(request):
        return httpx.Response(200, content=b"not json")

    with pytest.raises(SourceUnavailable):
        numeric_values("f", "e", 0, 1, client=_client(handler))


# --------------------------------------------------------- query shape ---

def test_query_hits_the_search_api_with_the_given_micro_window():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"hits": []})

    numeric_values("input_tokens", "agent.llm_call", 111, 222, client=_client(handler))
    assert "/_search" in seen["url"]
    assert seen["body"]["query"]["start_time"] == 111
    assert seen["body"]["query"]["end_time"] == 222
    sql = seen["body"]["query"]["sql"]
    assert "input_tokens" in sql
    assert "agent.llm_call" in sql


# -------------------------------------------------------------- metrics ---

def test_metric_value_reads_the_metric_as_its_own_stream():
    # OpenObserve stores each metric as a stream named after the metric, with
    # the sample in a `value` column, so the query must be FROM "<metric>",
    # not a metric_name filter on a shared stream.
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"hits": [{"value": 203870208.0}]})

    val = metric_value("otelcol_process_memory_rss", 0, 1, client=_client(handler))
    assert val == pytest.approx(203870208.0)
    sql = seen["body"]["query"]["sql"]
    assert '"otelcol_process_memory_rss"' in sql
    assert "metric_name" not in sql


def test_metric_value_returns_none_when_metric_stream_is_empty():
    def handler(request):
        return httpx.Response(200, json={"hits": []})

    assert metric_value("otelcol_process_memory_rss", 0, 1, client=_client(handler)) is None


# ------------------------------------------------------------- windows ---

def test_windows_are_contiguous_ordered_and_correctly_sized():
    now = 1_000_000_000_000
    baseline, live = windows(now, live_minutes=15, baseline_minutes=60)
    # live ends now, baseline ends exactly where live begins (contiguous)
    assert live[1] == now
    assert baseline[1] == live[0]
    # correct durations in microseconds
    assert live[1] - live[0] == 15 * 60 * 1_000_000
    assert baseline[1] - baseline[0] == 60 * 60 * 1_000_000
    # strictly ordered
    assert baseline[0] < baseline[1] < live[1]
