"""The single mapping table from telemetry signals to dashboard tiles.

SEMCONV.md's discipline: keep every name in one place so a rename is a one-line
edit, not a hunt across query builders and tests. Every tile the dashboard
renders is declared here once, with either the signal that backs it or the
reason it is currently unavailable.

Tile ids and labels match dashboard-ui/src/mockData.ts exactly, and the order is
preserved, so the live payload lands in the existing panels without touching
the layout.
"""

from __future__ import annotations

from dataclasses import dataclass

# The service.name the orchestrator actually emits today. SEMCONV.md records
# this as an open naming decision (`backend-api` vs `agent-orchestrator`) to be
# settled with the orchestrator team; until then we mirror what the code emits,
# and keep it here so the decision stays a one-line change.
SERVICE_NAME = "backend-api"


@dataclass(frozen=True)
class DriftSignal:
    """One drift tile and the telemetry signal (if any) that backs it."""

    id: str
    label: str
    # "continuous" -> PSI over a numeric field's distribution.
    # "categorical" -> PSI over the mix of a discrete field's values.
    # None -> not backed yet; rendered unavailable with `reason`.
    kind: str | None
    # The log event / span name to filter on, and the field to read from it.
    event: str | None = None
    field: str | None = None
    reason: str | None = None


# Drift Condition panel. Order matches the UI. Three are backed by real
# distributions we already collect; two need ground-truth labels a chat agent
# does not produce, so they are honestly marked unavailable rather than mocked.
DRIFT_SIGNALS: list[DriftSignal] = [
    DriftSignal(
        id="concept-drift",
        label="Concept Drift",
        kind=None,
        reason="needs ground-truth labels; not derivable from operational telemetry",
    ),
    DriftSignal(
        id="covariate-shift",
        label="Covariate Shift",
        kind="continuous",
        event="agent.llm_call",
        field="input_tokens",
    ),
    DriftSignal(
        id="label-drift",
        label="Label Drift",
        kind=None,
        reason="needs ground-truth labels; not derivable from operational telemetry",
    ),
    DriftSignal(
        id="feature-drift",
        label="Feature Drift",
        kind="categorical",
        event="agent.step_completed",
        field="agent",
    ),
    DriftSignal(
        id="prediction-drift",
        label="Prediction Drift",
        kind="continuous",
        event="agent.llm_call",
        field="output_tokens",
    ),
]

# Technical/quality panel. Every one of these needs the actual prompt/response
# text run through an eval or LLM-judge pipeline (Slice B), which also touches
# the redaction policy. Declared here, honestly unavailable, so the panel keeps
# its shape and the provenance is explicit.
TECHNICAL_TILES: list[tuple[str, str]] = [
    ("completeness", "Completeness"),
    ("avoidance", "Avoidance"),
    ("hallucination", "Hallucination"),
    ("excessive-sentiment", "Excessive Sentiment"),
    ("excessive-agency", "Excessive Agency"),
    ("accuracy", "Accuracy"),
    ("contexted", "Contexted"),
    ("relevancy", "Relevancy"),
    ("latency", "Latency"),
    ("toxicity", "Toxicity"),
    ("fluency", "Fluency"),
]
TECHNICAL_UNAVAILABLE_REASON = (
    "needs a content-eval / LLM-judge pipeline (Slice B); collides with the "
    "metadata-only redaction policy"
)


@dataclass(frozen=True)
class ResourceSignal:
    """One resource tile and the Collector self-metric that backs it."""

    id: str
    label: str
    # Prometheus metric name exposed by the Collector's own telemetry, or None
    # if we cannot back this tile yet.
    metric: str | None = None
    reason: str | None = None


# Resource panel. Memory and compute come from the Collector's own
# otelcol_process_* self-metrics. Storage and bandwidth have no equivalent
# signal in this stack yet.
RESOURCE_SIGNALS: list[ResourceSignal] = [
    ResourceSignal(id="memory", label="Memory", metric="otelcol_process_memory_rss"),
    ResourceSignal(id="compute", label="Compute", metric="otelcol_process_cpu_seconds"),
    ResourceSignal(
        id="storage",
        label="Storage",
        reason="no storage-utilisation signal in this stack yet",
    ),
    ResourceSignal(
        id="bandwidth",
        label="Bandwidth",
        reason="no bandwidth signal in this stack yet",
    ),
]

# Corrective actions are UI affordances, not data. Returned as-is so the panel
# renders; wiring them to real remediation is deliberately out of scope (the
# observability plane never writes back to the observed system).
CORRECTIVE_ACTIONS: list[tuple[str, str]] = [
    ("micro-retrain", "Micro Retrain"),
    ("micro-randomize", "Micro Randomize"),
    ("covariate-reset", "Covariate Reset"),
    ("revert-model", "Revert Model"),
]
