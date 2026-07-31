"""Regression tests for OpenObserve metric stream handling."""

from __future__ import annotations

import json

import httpx
import pytest

from app.source import metric_value


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_metric_value_normalizes_dotted_otel_metric_name():
    """Dotted OTEL names must use OpenObserve's underscore stream names."""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)

        return httpx.Response(
            200,
            json={"hits": [{"value": 0.41}]},
        )

    value = metric_value(
        "system.filesystem.utilization",
        0,
        1,
        client=_client(handler),
    )

    assert value == pytest.approx(0.41)

    sql = seen["body"]["query"]["sql"]

    assert '"system_filesystem_utilization"' in sql
    assert '"system.filesystem.utilization"' not in sql
    assert "type=metrics" in seen["url"]


def test_metric_value_computes_busy_cpu_from_latest_idle_rows():
    """CPU utilization is one minus average idle across the latest CPU rows."""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)

        return httpx.Response(
            200,
            json={
                "hits": [
                    # Latest collection: average idle is 0.75.
                    {
                        "_timestamp": 200,
                        "cpu": "cpu0",
                        "state": "idle",
                        "value": 0.70,
                    },
                    {
                        "_timestamp": 200,
                        "cpu": "cpu1",
                        "state": "idle",
                        "value": 0.80,
                    },
                    # Other CPU states must not be used as total utilization.
                    {
                        "_timestamp": 200,
                        "cpu": "cpu0",
                        "state": "wait",
                        "value": 0.08,
                    },
                    {
                        "_timestamp": 200,
                        "cpu": "cpu1",
                        "state": "user",
                        "value": 0.12,
                    },
                    # Older idle rows must not be included.
                    {
                        "_timestamp": 100,
                        "cpu": "cpu0",
                        "state": "idle",
                        "value": 0.95,
                    },
                    {
                        "_timestamp": 100,
                        "cpu": "cpu1",
                        "state": "idle",
                        "value": 0.95,
                    },
                ]
            },
        )

    value = metric_value(
        "system.cpu.utilization",
        0,
        1_000,
        client=_client(handler),
    )

    # 1 - average(0.70, 0.80) = 0.25.
    assert value == pytest.approx(0.25)

    sql = seen["body"]["query"]["sql"]

    assert '"system_cpu_utilization"' in sql
    assert "_timestamp" in sql
    assert "cpu" in sql
    assert "state" in sql
    assert "value" in sql
    assert "type=metrics" in seen["url"]