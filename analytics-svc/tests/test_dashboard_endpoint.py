"""Contract tests for GET /dashboard.

The telemetry backend is stubbed at the `source` layer, so these assert the
assembler + endpoint + schema wiring deterministically: the payload matches the
UI contract, provenance is honest, real drift moves the gauges, and a dead
backend still yields a valid all-unavailable payload with HTTP 200.

Run from analytics-svc/:
    python -m pytest tests/test_dashboard_endpoint.py -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import dashboard as dash
from app import source
from app.main import app
from app.schemas import DashboardData

client = TestClient(app)

FIXED_NOW = 10**15  # fixed microsecond "now" so windows are deterministic


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(source, "now_microseconds", lambda: FIXED_NOW)
    return FIXED_NOW


def _live_end() -> int:
    # Must match app.source.windows: the live window ends exactly at now.
    return FIXED_NOW


def _stub_sources(monkeypatch, *, baseline_num, live_num, baseline_cat, live_cat, memory_bytes):
    """Point every source read at canned data, split by window end time."""

    def fake_numeric(field, event, start, end, *, client=None):
        return list(live_num) if end == _live_end() else list(baseline_num)

    def fake_categorical(field, event, start, end, *, client=None):
        return list(live_cat) if end == _live_end() else list(baseline_cat)

    def fake_metric(metric_name, start, end, *, client=None):
        return memory_bytes

    monkeypatch.setattr(source, "numeric_values", fake_numeric)
    monkeypatch.setattr(source, "categorical_values", fake_categorical)
    monkeypatch.setattr(source, "metric_value", fake_metric)


# ---------------------------------------------------------------- health ---

def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "analytics-svc"


# -------------------------------------------------------------- contract ---

def test_dashboard_matches_the_ui_contract(frozen_now, monkeypatch):
    _stub_sources(
        monkeypatch,
        baseline_num=list(range(0, 100)),
        live_num=list(range(0, 100)),
        baseline_cat=["db_agent"] * 50 + ["api_agent"] * 50,
        live_cat=["db_agent"] * 50 + ["api_agent"] * 50,
        memory_bytes=128 * 1024 * 1024,
    )
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    data = DashboardData.model_validate(resp.json())  # raises if the shape is wrong

    assert len(data.driftMetrics) == 5
    assert len(data.technicalMetrics) == 11
    assert len(data.resourceMetrics) == 4
    assert len(data.correctiveActions) == 4

    valid_sources = {"mock", "instrumented", "inferred", "unavailable"}
    for tile in (*data.driftMetrics, *data.technicalMetrics, *data.resourceMetrics):
        assert tile.source in valid_sources
        assert tile.band in {"low", "medium", "high"}


def test_ids_and_order_match_the_mock_the_ui_already_renders(frozen_now, monkeypatch):
    _stub_sources(
        monkeypatch,
        baseline_num=list(range(0, 100)),
        live_num=list(range(0, 100)),
        baseline_cat=["db_agent", "api_agent"],
        live_cat=["db_agent", "api_agent"],
        memory_bytes=None,
    )
    data = DashboardData.model_validate(client.get("/dashboard").json())
    drift_ids = [d.id for d in data.driftMetrics]
    assert drift_ids == [
        "concept-drift",
        "covariate-shift",
        "label-drift",
        "feature-drift",
        "prediction-drift",
    ]


# ------------------------------------------------------------ provenance ---

def test_real_drift_moves_the_gauge_and_is_marked_inferred(frozen_now, monkeypatch):
    # Live windows shifted well away from baseline for both continuous signals
    # and the categorical mix.
    _stub_sources(
        monkeypatch,
        baseline_num=list(range(0, 100)),
        live_num=[v + 70 for v in range(0, 100)],
        baseline_cat=["db_agent"] * 90 + ["api_agent"] * 10,
        live_cat=["db_agent"] * 10 + ["api_agent"] * 90,
        memory_bytes=64 * 1024 * 1024,
    )
    data = DashboardData.model_validate(client.get("/dashboard").json())
    by_id = {d.id: d for d in data.driftMetrics}

    for tid in ("covariate-shift", "prediction-drift", "feature-drift"):
        assert by_id[tid].source == "inferred"
        assert by_id[tid].value > 0
        assert by_id[tid].band in {"medium", "high"}


def test_label_and_concept_drift_are_always_unavailable(frozen_now, monkeypatch):
    _stub_sources(
        monkeypatch,
        baseline_num=list(range(0, 100)),
        live_num=list(range(0, 100)),
        baseline_cat=["db_agent", "api_agent"],
        live_cat=["db_agent", "api_agent"],
        memory_bytes=64 * 1024 * 1024,
    )
    data = DashboardData.model_validate(client.get("/dashboard").json())
    by_id = {d.id: d for d in data.driftMetrics}
    assert by_id["concept-drift"].source == "unavailable"
    assert by_id["label-drift"].source == "unavailable"


def test_memory_tile_is_instrumented_from_the_collector_metric(frozen_now, monkeypatch):
    _stub_sources(
        monkeypatch,
        baseline_num=list(range(0, 100)),
        live_num=list(range(0, 100)),
        baseline_cat=["db_agent"],
        live_cat=["db_agent"],
        memory_bytes=256 * 1024 * 1024,  # half of the 512 MiB limit -> ~50%
    )
    data = DashboardData.model_validate(client.get("/dashboard").json())
    mem = next(r for r in data.resourceMetrics if r.id == "memory")
    assert mem.source == "instrumented"
    assert mem.percent == pytest.approx(50.0, abs=1.0)
    assert mem.band == "low"


def test_all_technical_tiles_are_unavailable_slice_b(frozen_now, monkeypatch):
    _stub_sources(
        monkeypatch,
        baseline_num=list(range(0, 100)),
        live_num=list(range(0, 100)),
        baseline_cat=["db_agent"],
        live_cat=["db_agent"],
        memory_bytes=None,
    )
    data = DashboardData.model_validate(client.get("/dashboard").json())
    assert all(t.source == "unavailable" for t in data.technicalMetrics)


# ------------------------------------------------------------- fail-open ---

def test_backend_down_yields_all_unavailable_and_http_200(frozen_now, monkeypatch):
    def boom(*args, **kwargs):
        raise source.SourceUnavailable("backend down")

    monkeypatch.setattr(source, "numeric_values", boom)
    monkeypatch.setattr(source, "categorical_values", boom)
    monkeypatch.setattr(source, "metric_value", boom)

    resp = client.get("/dashboard")
    assert resp.status_code == 200  # never a 500
    data = DashboardData.model_validate(resp.json())
    assert all(d.source == "unavailable" for d in data.driftMetrics)
    assert all(r.source == "unavailable" for r in data.resourceMetrics)
    # the payload is still structurally complete
    assert len(data.driftMetrics) == 5
    assert len(data.correctiveActions) == 4


def test_unexpected_error_still_returns_valid_payload(frozen_now, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("unexpected")

    # An error that is NOT SourceUnavailable/InsufficientData must still be
    # caught by the endpoint's last-resort wrapper.
    monkeypatch.setattr(dash, "build_dashboard", explode)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    DashboardData.model_validate(resp.json())
