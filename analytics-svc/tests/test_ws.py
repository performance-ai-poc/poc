"""Tests for the real-time WebSocket dashboard push (/ws/dashboard).

The socket reads the shared in-memory cache that the background refresher keeps
warm, so these tests drive that cache directly and assert exactly what the
socket sends. No background task, no OpenObserve, fully deterministic.

Run from analytics-svc/:
    python -m pytest tests/test_ws.py -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import live
from app.config import settings
from app.main import app
from app.schemas import (
    CorrectiveAction,
    DashboardData,
    DriftMetric,
    ResourceMetric,
    TechnicalMetric,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_cache():
    """Each test starts with an empty cache and leaves it empty, so nothing
    leaks between tests through the module-level singleton."""
    live.cache._data = None
    yield
    live.cache._data = None


def _marker_dashboard(value: float = 77.0) -> DashboardData:
    """A tiny, distinctive payload so a test can prove the socket sent exactly
    what was in the cache."""
    return DashboardData(
        driftMetrics=[
            DriftMetric(id="covariate-shift", label="Covariate Shift", value=value, band="high", source="inferred")
        ],
        technicalMetrics=[
            TechnicalMetric(id="latency", label="Latency", value=0.0, band="low", source="unavailable")
        ],
        resourceMetrics=[
            ResourceMetric(id="memory", label="Memory", percent=value, band="low", source="instrumented")
        ],
        correctiveActions=[CorrectiveAction(id="revert-model", label="Revert Model", enabled=True)],
    )


# ------------------------------------------------------------------ cache ---

def test_cache_starts_empty_and_stores_last_snapshot():
    from app.live import DashboardCache

    c = DashboardCache()
    assert c.get() is None
    snap = _marker_dashboard()
    c.set(snap)
    assert c.get() is snap


# --------------------------------------------------------------- websocket ---

def test_ws_sends_a_valid_payload_before_the_first_compute():
    # Cache is empty (refresher not running in tests): the socket must still
    # send a valid, correctly-shaped all-unavailable payload, never nothing.
    with client.websocket_connect("/ws/dashboard") as ws:
        msg = ws.receive_json()
    data = DashboardData.model_validate(msg)
    assert len(data.driftMetrics) == 5
    assert len(data.technicalMetrics) == 11
    assert len(data.resourceMetrics) == 4
    assert all(t.source == "unavailable" for t in data.driftMetrics)


def test_ws_pushes_exactly_what_is_in_the_cache():
    live.cache.set(_marker_dashboard(value=77.0))
    with client.websocket_connect("/ws/dashboard") as ws:
        msg = ws.receive_json()
    data = DashboardData.model_validate(msg)
    assert data.driftMetrics[0].id == "covariate-shift"
    assert data.driftMetrics[0].value == 77.0
    assert data.resourceMetrics[0].percent == 77.0


def test_ws_pushes_continuously(monkeypatch):
    monkeypatch.setattr(settings, "ws_push_seconds", 0.02)  # fast for the test
    live.cache.set(_marker_dashboard(value=42.0))
    with client.websocket_connect("/ws/dashboard") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    assert first["driftMetrics"][0]["value"] == 42.0
    assert second["driftMetrics"][0]["value"] == 42.0


def test_ws_reflects_live_cache_updates(monkeypatch):
    # The whole point of real-time: when the refresher updates the cache, the
    # next pushes carry the new values.
    monkeypatch.setattr(settings, "ws_push_seconds", 0.02)
    live.cache.set(_marker_dashboard(value=10.0))
    with client.websocket_connect("/ws/dashboard") as ws:
        first = ws.receive_json()
        live.cache.set(_marker_dashboard(value=99.0))
        latest = first
        for _ in range(50):  # let a few pushes go by until it flips
            latest = ws.receive_json()
            if latest["driftMetrics"][0]["value"] == 99.0:
                break
    assert first["driftMetrics"][0]["value"] == 10.0
    assert latest["driftMetrics"][0]["value"] == 99.0
