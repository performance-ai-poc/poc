# BOUNDARY — What this workstream owns, and what it does not

Scope note for the OTel infrastructure workstream. Frontend and
backend/orchestrator work belong to other teams; this document records only
where the seams are, not what those teams should build.

---

## Owned by this workstream

| Area | Paths |
|---|---|
| Collector configuration | `otel/collector-config.yaml` |
| Local telemetry stack | `otel/docker-compose.otel.yml` |
| Capture policy layer | `otel/policy/` |
| Collector Kubernetes manifests | `infra/helm/ai-chat/templates/otel-collector-*.yaml` |
| Collector RBAC, mounts, resource limits | same |
| Telemetry backend deployment | `otel/` and Helm |
| Telemetry acceptance tests | `otel/tests/` |
| Everything else under `otel/` | — |

## Not owned by this workstream

| Area | Owner |
|---|---|
| `orchestrator-svc/` application code | Orchestrator team |
| `mcp-server/` application code | MCP team |
| `customer-ui/`, `dashboard-ui/` | Frontend team |
| Agent logic, routing, LLM integration | Orchestrator team |
| What the dashboard renders | Frontend team |

---

## Seam 1: application stdout → Collector

**This seam requires nothing from anyone.** Both services already write
structured JSON to stdout, one object per line, carrying four correlation IDs.
The Collector's `filelog` receiver reads the container log path and turns all of
it into queryable telemetry with no change to any application file.

Fields already present on every line and usable as attributes:
`run_id`, `request_id`, `session_id`, `tenant_id`, `service.name`, `event`,
`timestamp`.

This is the whole of Phase 2 and it is entirely within this workstream.

## Seam 2: application OTLP → Collector

The Collector exposes OTLP on `4317` (gRPC) and `4318` (HTTP). If and when the
orchestrator team adds an OTel SDK, they point it at that endpoint via standard
environment variables. Nothing in the Collector config needs to change to
receive it.

**Instrumentation inside `orchestrator-svc/` and `mcp-server/` is not this
workstream's task.** See "Handoffs" below.

## Seam 3: Collector → telemetry backend

OTLP over TLS to OpenObserve, with a bounded persistent queue. Owned here
end to end.

## Seam 4: backend → whatever consumes it

The telemetry backend exposes a query API. Whether the dashboard reads it
directly or through a service in front is the frontend team's decision, not a
constraint from this side.

---

## Handoffs — needed from other teams, not tasks for this one

Record these so they are visible, and so nobody assumes this workstream is
building them.

| Handoff | Needed from | Effect if it does not land |
|---|---|---|
| OTel SDK wired into the orchestrator, exporting OTLP | Orchestrator team | No real spans. Phase 2 filelog telemetry still works; correlation stays `run_id`-based and is reported as `inferred`. |
| `traceparent` carried in the existing `params._meta` channel | Orchestrator + MCP | No parent/child spans across the process boundary. |
| `route.reason_code` emitted on routing decisions | Orchestrator team | The routing rationale stays invisible. Nothing in the collection plane can derive it. |
| Content attributes emitted at all | Orchestrator team | The policy layer still works, but has nothing to gate on for content. |
| A dashboard that renders telemetry | Frontend team | Backend UI is the fallback view. |

None of these block Phases 1, 2, 6, 7, or 8. The workstream is complete and
demonstrable without any of them.

---

## What this workstream can demonstrate alone

Without a single change from another team:

1. Collector running, receiving, redacting, exporting
2. Every existing `api.request.*` and `agent.*` event queryable in the backend,
   correlated by `run_id`, application untouched
3. Capture policy switching what reaches the backend
4. Collector killed mid-session, application unaffected, stdout logging
   continuing
5. Read-only RBAC and bounded resources on the Collector — the first workload in
   the chart with either

That is a complete story. Anything the other teams add makes it richer, but
nothing they do or do not do prevents it.
