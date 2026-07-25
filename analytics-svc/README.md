# analytics-svc

Read-only drift/analytics plane in front of the telemetry backend. It turns the
operational telemetry the observability plane already collects into the drift
metrics the dashboard renders.

It is a separate service from `orchestrator-svc` on purpose: a slow or failing
drift query can never touch the chat request path, and the whole thing fails
open — if the backend is unreachable, the dashboard shows greyed-out
(`unavailable`) tiles instead of anything breaking.

## What it computes

The dashboard's Drift Condition panel is backed by real distribution drift,
measured with PSI (Population Stability Index) comparing a baseline time window
against the most recent live window:

| Tile | Signal | Provenance |
|---|---|---|
| Covariate Shift | input-token distribution | `inferred` (computed) |
| Feature Drift | agent-routing mix | `inferred` (computed) |
| Prediction Drift | output-token distribution | `inferred` (computed) |
| Memory, Compute | Collector self-metrics | `instrumented` |
| Concept / Label Drift | needs ground-truth labels | `unavailable` |
| Quality tiles (hallucination, toxicity, …) | needs a content-eval pipeline (Slice B) | `unavailable` |

Every tile carries a `source`, so the dashboard never presents a computed or
missing value as if it were a direct measurement.

## Endpoints

- `GET /health` — liveness, no dependency on the backend.
- `GET /dashboard` — the full dashboard payload, typed to
  `dashboard-ui/src/types.ts`.

Behind the dashboard's nginx it is reached at `/api/analytics/*`.

## Run locally

```bash
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -r requirements.txt
python -m app.main            # serves on :8002
```

## Test

```bash
python -m pytest -q
```

## Configuration

All settings come from the environment (see `.env.example`). The service never
writes to any system it observes.
