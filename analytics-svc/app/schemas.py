"""Response schema for the dashboard endpoint.

These models are the server-side mirror of dashboard-ui/src/types.ts. The field
names and the Source/Band vocabularies match it exactly, so the JSON this
service returns drops straight into the existing dashboard with no adapter. If
the UI contract changes, this file is the one place to change in step.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Band = Literal["low", "medium", "high"]

# Provenance of every tile, mirroring the UI's own Source union:
#   instrumented - a real measured value from telemetry we collect directly
#   inferred     - a value we computed/derived (e.g. a PSI drift score)
#   unavailable  - we cannot back this tile yet; the UI renders it greyed out
#   mock         - placeholder; this service never emits it, the UI owns the mocks
Source = Literal["mock", "instrumented", "inferred", "unavailable"]


class DriftMetric(BaseModel):
    id: str
    label: str
    value: float
    band: Band
    source: Source


class TechnicalMetric(BaseModel):
    id: str
    label: str
    value: float
    band: Band
    source: Source


class ResourceMetric(BaseModel):
    id: str
    label: str
    percent: float | None = None
    value: float | None = None
    unit: str | None = None
    band: Band
    source: Source


class CorrectiveAction(BaseModel):
    id: str
    label: str
    enabled: bool


class DashboardData(BaseModel):
    driftMetrics: list[DriftMetric]
    technicalMetrics: list[TechnicalMetric]
    resourceMetrics: list[ResourceMetric]
    correctiveActions: list[CorrectiveAction]
