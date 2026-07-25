# OTel — Observability plane

End-to-end OpenTelemetry for the AI chat POC. Two parts:

1. Application instrumentation — a vendor-neutral OTel SDK inside
   `orchestrator-svc/` and `mcp-server/` producing real spans, metrics, and logs
   (HTTP, agent, LLM, tool, and DB), with W3C trace context propagated across the
   MCP boundary. Gated by `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_SDK_DISABLED` — no
   endpoint means no export, and the app is unaffected either way.
2. The collection plane (this folder + the Helm templates) — an OTel Collector
   that receives that OTLP, redacts sensitive data, enforces a capture policy,
   and exports to OpenObserve. Also tails stdout via `filelog` as a correlation
   backstop, and scrapes host/pod/self metrics.

The two are independent: stop the Collector and the app keeps serving requests
and logging to stdout (fail-open, `docs/CONSTRAINTS.md` C2).

## Signal flow

```
orchestrator-svc ─┐  OTLP (real spans/metrics/logs, traceparent in _meta)
                  ├───────────────►  OTel Collector (DaemonSet / compose)
mcp-server ───────┘                    memory_limiter → resource → k8sattributes
        │  stdout JSON ──filelog──►     → attributes/privacy (redaction)
        │                                → transform/limits (capture policy +
        │                                   fail-closed allowlist + normalize)
        │                                → filter/noise → batch
        │                                        │ otlphttp over the bounded queue
        ▼                                        ▼
   (never calls the Collector)            OpenObserve  (traces + metrics + logs
                                                        + dashboard)
```

## What the collection plane guarantees

- **Metadata-only by default.** Prompts/completions (`gen_ai.input.messages` /
  `gen_ai.output.messages`) are deleted unless the capture policy is
  `content-approved`. `gen_ai.system_instructions` is never permitted.
- **Redaction of what auto-instrumentation would otherwise leak** — raw SQL
  (`db.statement` / `db.query.text` from psycopg), full outbound URLs
  (`http.url` / `url.full` / query strings from httpx), auth headers, cookies,
  api keys; `user.email` / `user.id` / `session.id` are hashed.
- **Fail-closed allowlist** (C4): a span/log attribute not explicitly permitted
  is dropped. The allowlist is comprehensive over the app's `gen_ai.*` / `app.*`
  attributes and the standard HTTP/DB semconv the instrumentation emits.
- **One correlation key.** Spans carry `app.run_id`; the Collector normalizes it
  (and the other three IDs) to bare `run_id` so a span and a log line for the
  same request join on the same field. Real trace context →
  `correlation.confidence=high`; filelog-only logs → `medium`.

## Start here

| Doc | What |
|---|---|
| [`docs/BOUNDARY.md`](docs/BOUNDARY.md) | Ownership split |
| [`docs/OTEL_PLAN.md`](docs/OTEL_PLAN.md) | What exists / build phases |
| [`docs/CONSTRAINTS.md`](docs/CONSTRAINTS.md) | Hard rules C1–C10 |
| [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) | Tests that define done |
| [`docs/SEMCONV.md`](docs/SEMCONV.md) | Event/attribute → OTel mapping |
| [`HANDOFF.md`](HANDOFF.md) | Cross-team items and what's now resolved |
| [`VERIFICATION_STATUS.md`](VERIFICATION_STATUS.md) | Exactly what's been run vs. only written |

## Layout

| Path | What |
|---|---|
| `collector-config.yaml` | The hardened Collector config (compose + the canonical copy the k8s ConfigMap mirrors) |
| `docker-compose.otel.yml` | Collector + OpenObserve, telemetry plane in isolation |
| `policy/` | Capture-mode gate (`metadata-only` / `content-approved`) + `apply.sh` |
| `tests/` | Acceptance scripts + offline Python suites (see below) |
| `local-logs/` | Local stand-in for the k8s container-log path (filelog source) |

The Kubernetes side is an independent chart in
[`infra/helm/observability/`](../infra/helm/observability/):
`otel-collector-{daemonset,configmap,rbac,secret}.yaml` and
`openobserve-{deployment,service,secret,pvc}.yaml`.

## Running it

**Full stack on Kubernetes (two independent Helm releases):**
```bash
make dev
```
OpenObserve is exposed on NodePort `30083`.

Deploy either ownership boundary independently:

```bash
make deploy-observability
make deploy-app
```

**Local data-plane + telemetry via Compose:**
```bash
docker compose up --build     # Postgres + MCP + Collector + OpenObserve
# OpenObserve UI: http://localhost:5080  (admin@example-test.invalid / otel-poc-local-only)
```

**Telemetry plane alone:**
```bash
make otel-up        # Collector + OpenObserve only (otel/docker-compose.otel.yml)
```

**Switch capture policy (no application restart):**
```bash
./otel/policy/apply.sh content-approved
./otel/policy/apply.sh metadata-only
```

## Tests

Offline (no Docker/cluster) — run with `orchestrator-svc`'s venv:
```bash
cd orchestrator-svc
./.venv/Scripts/python.exe -m pytest \
  ../otel/tests/test_offline_config.py \
  ../otel/tests/test_span_contract.py \
  ../otel/tests/test_log_contract.py \
  ../otel/tests/test_trace_propagation.py
```
These validate config parity, that the app's real spans/logs are covered by the
allowlist with nothing sensitive leaking, and that trace context round-trips
across the MCP boundary. `test_rbac.sh` and `test_resources.sh` run static
checks against the rendered chart.

Live (need Docker/cluster): `test_collector_up.sh`, `test_redaction.sh`,
`test_filelog_ingest.sh`, `test_policy_switch.sh`, `test_failopen.sh`,
`test_saturation.sh`. See [`VERIFICATION_STATUS.md`](VERIFICATION_STATUS.md) for
what has and hasn't actually been executed.
