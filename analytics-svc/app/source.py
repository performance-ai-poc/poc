"""Read-only client for the telemetry backend (OpenObserve).

Pulls the raw values behind each drift tile out of the backend's search API,
over a given microsecond time window. This is the same query surface the
otel/tests shell scripts use: POST /api/{org}/_search with an SQL string and a
start/end window in microseconds.

Everything here is read-only and fail-open. Any transport or backend problem is
raised as SourceUnavailable, which the caller turns into an `unavailable` tile
rather than a failed request. A missing drift number is a grey gauge, never a
500.
"""

from __future__ import annotations

import time

import httpx

from app.config import settings


class SourceUnavailable(Exception):
    """The telemetry backend could not be queried (down, slow, or malformed
    response). Caller renders the affected tiles as `unavailable`."""


def now_microseconds() -> int:
    return int(time.time() * 1_000_000)


def windows(
    now_us: int, live_minutes: int, baseline_minutes: int
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Return (baseline_window, live_window) as microsecond [start, end] ranges.

    The live window is the most recent `live_minutes`. The baseline is the
    `baseline_minutes` immediately before it. Drift is how far the live
    distribution has moved from that baseline, so the two windows are contiguous
    and non-overlapping.
    """
    live_span = live_minutes * 60 * 1_000_000
    base_span = baseline_minutes * 60 * 1_000_000
    live = (now_us - live_span, now_us)
    baseline = (now_us - live_span - base_span, now_us - live_span)
    return baseline, live


def _search(
    sql: str,
    start_us: int,
    end_us: int,
    *,
    search_type: str | None = None,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Low-level query. Returns the `hits` array, or raises SourceUnavailable."""
    url = f"{settings.openobserve_url}/api/{settings.openobserve_org}/_search"
    if search_type:
        url += f"?type={search_type}"
    body = {
        "query": {
            "sql": sql,
            "start_time": start_us,
            "end_time": end_us,
            "size": 10000,
        }
    }
    headers = {
        "Authorization": settings.openobserve_auth,
        "Content-Type": "application/json",
    }
    owns_client = client is None
    c = client or httpx.Client(timeout=settings.openobserve_timeout_s)
    try:
        resp = c.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceUnavailable(str(exc)) from exc
    finally:
        if owns_client:
            c.close()
    hits = data.get("hits")
    return hits if isinstance(hits, list) else []


def numeric_values(
    field: str,
    event: str | tuple[str, ...],
    start_us: int,
    end_us: int,
    *,
    client: httpx.Client | None = None,
) -> list[float]:
    """Every numeric value of `field` from records with the given `event`, over
    the window. Non-numeric or null values are skipped, not fatal."""
    if isinstance(event, tuple):
        event_clause = " OR ".join([f"event = '{e}'" for e in event])
    else:
        event_clause = f"event = '{event}'"
    sql = (
        f'SELECT "{field}" AS value FROM "{settings.openobserve_stream}" '
        f"WHERE {event_clause}"
    )
    out: list[float] = []
    for hit in _search(sql, start_us, end_us, client=client):
        raw = hit.get("value", hit.get(field))
        if raw is None:
            continue
        try:
            out.append(float(raw))
        except (TypeError, ValueError):
            continue
    return out


def categorical_values(
    field: str,
    event: str | tuple[str, ...],
    start_us: int,
    end_us: int,
    *,
    client: httpx.Client | None = None,
) -> list[str]:
    """Every value of a discrete `field` from records with the given `event`,
    over the window, the raw material for categorical (mix) drift."""
    if isinstance(event, tuple):
        event_clause = " OR ".join([f"event = '{e}'" for e in event])
    else:
        event_clause = f"event = '{event}'"
    sql = (
        f'SELECT "{field}" AS label FROM "{settings.openobserve_stream}" '
        f"WHERE {event_clause}"
    )
    out: list[str] = []
    for hit in _search(sql, start_us, end_us, client=client):
        raw = hit.get("label", hit.get(field))
        if raw is None:
            continue
        out.append(str(raw))
    return out


def metric_value(
    metric_name: str,
    start_us: int,
    end_us: int,
    *,
    client: httpx.Client | None = None,
) -> float | None:
    """Latest value of a Collector self-metric over the window, or None if the
    metric is not present. Used for the resource tiles.

    OpenObserve stores each metric as its own stream (the metric name is the
    stream name), with the sample in a `value` column, so we read straight
    FROM the metric stream rather than filtering a metric_name column.
    """
    sql = f'SELECT value FROM "{metric_name}" ORDER BY _timestamp DESC LIMIT 1'
    hits = _search(sql, start_us, end_us, search_type="metrics", client=client)
    if not hits:
        return None
    raw = hits[0].get("value")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def metric_samples(
    metric_name: str,
    start_us: int,
    end_us: int,
    *,
    client: httpx.Client | None = None,
) -> list[tuple[int, float]]:
    """Return ordered (timestamp_us, value) samples for a metric stream."""
    sql = f'SELECT value, _timestamp FROM "{metric_name}" ORDER BY _timestamp ASC'
    hits = _search(sql, start_us, end_us, search_type="metrics", client=client)
    samples: list[tuple[int, float]] = []
    for hit in hits:
        raw = hit.get("value")
        ts = hit.get("_timestamp")
        try:
            value = float(raw)
            ts_us = int(ts)
        except (TypeError, ValueError):
            continue
        samples.append((ts_us, value))
    return samples


def metric_records(
    metric_name: str,
    start_us: int,
    end_us: int,
    *,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Return raw metric hits for callers that need attributes as well as value."""
    sql = f'SELECT value, _timestamp, * FROM "{metric_name}"'
    return _search(sql, start_us, end_us, search_type="metrics", client=client)
