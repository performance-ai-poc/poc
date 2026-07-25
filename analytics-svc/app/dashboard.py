"""The dashboard endpoint.

Assembles the DashboardData payload the UI expects by, for each tile:
  - pulling its baseline and live values from the telemetry backend (source),
  - computing PSI drift or reading a resource metric (psi),
  - tagging the result with honest provenance (schemas.Source).

Fail-open is enforced at three levels: each tile catches its own data problems
and degrades to `unavailable`; the whole build is wrapped so any unexpected
error still returns a valid all-unavailable payload with HTTP 200. The dashboard
never sees a 500 from this endpoint.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter

from app import metrics_map, psi, source
from app.config import settings
from app.schemas import (
    CorrectiveAction,
    DashboardData,
    DriftMetric,
    ResourceMetric,
    TechnicalMetric,
)

logger = logging.getLogger("analytics.dashboard")

router = APIRouter()

# Resource-tile banding: fraction of the limit consumed.
_RESOURCE_MEDIUM = 60.0
_RESOURCE_HIGH = 85.0


def _band_for_percent(percent: float) -> str:
    if percent < _RESOURCE_MEDIUM:
        return "low"
    if percent < _RESOURCE_HIGH:
        return "medium"
    return "high"


def _unavailable_drift(sig: metrics_map.DriftSignal) -> DriftMetric:
    # band is irrelevant when source is unavailable (the UI greys it out), but
    # must still be a valid value.
    return DriftMetric(id=sig.id, label=sig.label, value=0.0, band="low", source="unavailable")


def _drift_tile(
    sig: metrics_map.DriftSignal,
    baseline_win: tuple[int, int],
    live_win: tuple[int, int],
    client: httpx.Client,
) -> DriftMetric:
    if sig.kind is None:
        return _unavailable_drift(sig)
    try:
        if sig.kind == "continuous":
            base = source.numeric_values(sig.field, sig.event, *baseline_win, client=client)
            live = source.numeric_values(sig.field, sig.event, *live_win, client=client)
            if len(base) < 2 or not live:
                raise psi.InsufficientData("not enough data in one of the windows")
            edges = psi.quantile_edges(base)
            base_counts = psi.bin_counts(base, edges)
            live_counts = psi.bin_counts(live, edges)
        else:  # categorical
            base = source.categorical_values(sig.field, sig.event, *baseline_win, client=client)
            live = source.categorical_values(sig.field, sig.event, *live_win, client=client)
            if not base or not live:
                raise psi.InsufficientData("empty window")
            base_counts, live_counts = psi.category_counts(base, live)
        score = psi.psi(base_counts, live_counts)
    except (source.SourceUnavailable, psi.InsufficientData) as exc:
        logger.info("drift tile %s unavailable: %s", sig.id, exc)
        return _unavailable_drift(sig)
    return DriftMetric(
        id=sig.id,
        label=sig.label,
        value=float(psi.psi_display_percent(score)),
        band=psi.band_for_psi(score),
        source="inferred",
    )


def _resource_tile(
    sig: metrics_map.ResourceSignal,
    win: tuple[int, int],
    client: httpx.Client,
) -> ResourceMetric:
    if sig.metric is None:
        return ResourceMetric(id=sig.id, label=sig.label, percent=0.0, band="low", source="unavailable")
    try:
        raw = source.metric_value(sig.metric, *win, client=client)
    except source.SourceUnavailable as exc:
        logger.info("resource tile %s unavailable: %s", sig.id, exc)
        raw = None
    if raw is None:
        return ResourceMetric(id=sig.id, label=sig.label, percent=0.0, band="low", source="unavailable")
    # The only metric-backed resource tile today is Memory (RSS in bytes),
    # expressed as a percent of the Collector's configured memory limit.
    limit_bytes = settings.collector_memory_limit_mib * 1024 * 1024
    percent = min(100.0, max(0.0, raw / limit_bytes * 100.0)) if limit_bytes else 0.0
    return ResourceMetric(
        id=sig.id,
        label=sig.label,
        percent=round(percent, 1),
        band=_band_for_percent(percent),
        source="instrumented",
    )


def _technical_tiles() -> list[TechnicalMetric]:
    # Every quality tile needs a content-eval pipeline (Slice B); honestly
    # unavailable for now so the panel keeps its shape.
    return [
        TechnicalMetric(id=tid, label=label, value=0.0, band="low", source="unavailable")
        for tid, label in metrics_map.TECHNICAL_TILES
    ]


def _corrective_actions() -> list[CorrectiveAction]:
    return [CorrectiveAction(id=aid, label=label, enabled=True) for aid, label in metrics_map.CORRECTIVE_ACTIONS]


def _all_unavailable() -> DashboardData:
    """The last-resort payload: everything greyed out, still perfectly valid."""
    return DashboardData(
        driftMetrics=[_unavailable_drift(s) for s in metrics_map.DRIFT_SIGNALS],
        technicalMetrics=_technical_tiles(),
        resourceMetrics=[
            ResourceMetric(id=s.id, label=s.label, percent=0.0, band="low", source="unavailable")
            for s in metrics_map.RESOURCE_SIGNALS
        ],
        correctiveActions=_corrective_actions(),
    )


def build_dashboard(client: httpx.Client | None = None) -> DashboardData:
    now = source.now_microseconds()
    baseline_win, live_win = source.windows(
        now, settings.live_window_minutes, settings.baseline_window_minutes
    )
    owns_client = client is None
    c = client or httpx.Client(timeout=settings.openobserve_timeout_s)
    try:
        drift = [_drift_tile(s, baseline_win, live_win, c) for s in metrics_map.DRIFT_SIGNALS]
        resource = [_resource_tile(s, live_win, c) for s in metrics_map.RESOURCE_SIGNALS]
    finally:
        if owns_client:
            c.close()
    return DashboardData(
        driftMetrics=drift,
        technicalMetrics=_technical_tiles(),
        resourceMetrics=resource,
        correctiveActions=_corrective_actions(),
    )


@router.get("/dashboard", response_model=DashboardData)
def dashboard() -> DashboardData:
    try:
        return build_dashboard()
    except Exception:  # noqa: BLE001 — last-resort fail-open, never 500 the UI
        logger.exception("dashboard build failed; returning all-unavailable")
        return _all_unavailable()
