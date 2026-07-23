# OTel

Telemetry infrastructure for the AI chat POC — the OTel Collector, its
capture policy, its Kubernetes deployment, and the acceptance tests that
verify it. Owned end to end by the telemetry infrastructure workstream; see
[`docs/BOUNDARY.md`](docs/BOUNDARY.md) for exactly what that does and does
not include.

**Nothing here modifies `orchestrator-svc/` or `mcp-server/`.** Both already
write structured JSON to their own stdout; this Collector reads that stream
passively. See [`docs/OTEL_PLAN.md`](docs/OTEL_PLAN.md) for why that's the
whole point.

## Start here

- [`docs/BOUNDARY.md`](docs/BOUNDARY.md) — what this workstream owns
- [`docs/OTEL_PLAN.md`](docs/OTEL_PLAN.md) — what exists, what to build, build phases
- [`docs/CONSTRAINTS.md`](docs/CONSTRAINTS.md) — hard rules (C1-C10)
- [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) — the tests that define done
- [`docs/SEMCONV.md`](docs/SEMCONV.md) — event-to-span mapping spec
- [`HANDOFF.md`](HANDOFF.md) — open decisions and what other teams would need
- [`VERIFICATION_STATUS.md`](VERIFICATION_STATUS.md) — what has and hasn't actually been run

## Layout

| Path | What |
|---|---|
| `collector-config.yaml` | Standalone Collector config (Phases 1-3): `otlp` + `filelog` receivers, the six-processor privacy/allowlist chain, `otlphttp` exporter. |
| `docker-compose.otel.yml` | Collector + OpenObserve only — no application service. `make otel-up` / `make otel-down`. |
| `policy/` | Capture-mode gate (`metadata-only` / `content-approved`). `policy/apply.sh <mode>` switches it. |
| `local-logs/` | Local stand-in for the Kubernetes container-log path — bind-mounted into the Collector; a developer redirects `python -m app.main`'s stdout here by hand for local filelog testing. |
| `tests/` | Acceptance scripts, one per `docs/ACCEPTANCE.md` criterion. Each script's header names which criterion it verifies. |
| `docs/` | The five planning documents listed above. |

The Kubernetes-side Collector config lives in
[`infra/helm/ai-chat/templates/otel-collector-*.yaml`](../infra/helm/ai-chat/templates/)
(DaemonSet, RBAC, ConfigMap, Secret) — outside this folder because it's part
of the Helm chart, per `docs/BOUNDARY.md`.

## Running it locally

```bash
make otel-up          # Collector + OpenObserve
make otel-test         # the two acceptance scripts that don't need the app running
./otel/tests/test_filelog_ingest.sh   # needs orchestrator-svc/.venv set up first
make otel-down
```

OpenObserve UI: http://localhost:5080 (`admin@example-test.invalid` /
`otel-poc-local-only` — local-only demo credentials, same posture the rest of
this repo takes for Postgres).

## Verification status

As of this writing, most of this folder's contents have been reviewed and
statically checked (Helm rendering, RBAC/resource assertions against the
rendered manifests) but not run against a live Collector or cluster — Docker
Desktop wasn't running in the environment this was built in. See
[`VERIFICATION_STATUS.md`](VERIFICATION_STATUS.md) for exactly what has and
hasn't been executed, and the specific risks flagged for whatever runs first.
