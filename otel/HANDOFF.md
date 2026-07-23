# HANDOFF — cross-team items

Records integration points and cross-team decisions. Now that the app-side SDK
work and the collection plane are merged on this branch, several previously-open
handoffs are **resolved** — noted first — and a couple remain.

## Resolved on this combined branch

- **OTel SDK wired into the app, exporting OTLP.** `orchestrator-svc` and
  `mcp-server` now produce real spans/metrics/logs (`app/telemetry.py` in each).
  The Collector's `otlp` receiver ingests them; the old "no real spans, run_id
  only" fallback no longer applies when the SDK is enabled.
- **`traceparent` across the MCP boundary.** Carried in the existing
  `params._meta` channel (orchestrator `mcp_client._correlation_meta` injects;
  mcp `telemetry.extract_context` rebuilds). The `mcp.tool` span parents on the
  orchestrator's `execute_tool` span — one distributed trace.
  `correlation.confidence` is stamped `high` on real spans.
- **Content-capture gate exists.** `otel/policy/` + the Collector's
  `transform/limits` gate `gen_ai.input.messages` / `gen_ai.output.messages` on
  `capture.mode`. The agent team may emit content; the Collector decides whether
  it survives — customer-controlled, no app change to toggle.

## Still open — a joint call, not decided here

### 1. `service.name` — now TWO values, needs reconciling

The combined branch actually made this sharper: there are now **two**
`service.name` values for the orchestrator, from two paths.

- The **OTLP spans/metrics** carry `service.name = "orchestrator-svc"` (the
  resource attribute, from `OTEL_SERVICE_NAME` in the deployment env /
  `values.yaml`).
- The **stdout JSON logs** carry `service.name = "backend-api"`
  (`orchestrator-svc/app/logging_utils.py::SERVICE_NAME`), which the filelog
  path reads verbatim.

So in the backend, a trace and its own service's logs are tagged with different
service names. That is a real correlation snag for anyone building dashboards.
The Collector does not paper over it (it passes both through as emitted), so
whoever owns dashboards should get the two aligned — pick one value and set both
`OTEL_SERVICE_NAME` and the logger's `SERVICE_NAME` constant to it. Not decided
here.

### 2. `agent.*` event-name vocabulary conflict

Same README section: the implemented vocabulary (`agent.step_started`,
`agent.tool_selected`, `agent.tool_returned`, `agent.retried`, ...) differs
from an earlier OTel design doc's example vocabulary (`agent.selected`,
`agent.tool.selected`, `agent.tool.result`, `agent.retry`, ...). Also
unresolved: the event-schema key itself (`"event"` in the implemented code vs.
`"event.type"` in the earlier doc's example), and whether `trace_id`/`span_id`
belong in the JSON payload (implemented code correctly omits them — see
Acceptance A6).

**This workstream mirrors whatever the code currently emits.** Every mapped
event name lives in exactly one table: the comment block above the
`transform/limits` processor in `otel/collector-config.yaml`, which mirrors
`docs/SEMCONV.md` §3. If either document's vocabulary is chosen as canonical,
updating the mapping is a one-line-per-row edit in that single table, not a
re-architecture.

### 3. `route.reason_code` — not yet emitted

`app/orchestrator/routing.py::pick_agent` makes a real routing decision
(which keyword rule fired) that is not captured anywhere today. This is the
single most valuable attribute the agent team could add, because it is the
one thing a passive collection layer genuinely cannot reconstruct after the
fact — by the time a log line exists, the "why" is gone.

Suggested enum (agent team owns the final list — see `SEMCONV.md` §5):
```
DOCUMENT_INTENT
SUPPORT_INTENT
COMPLIANCE_INTENT
API_INTENT
FALLBACK_DEFAULT
```
Enum only, attached to the `invoke_agent` span. Never free text, never model
reasoning.

**Effect if it never lands:** the routing rationale stays invisible. Nothing
in the collection plane can derive it from the outside — this is a genuine
gap, not a nice-to-have.

---

## What the orchestrator/MCP teams would need to wire an OTel SDK

Recorded as a spec to hand over, not a task this workstream executes (see
`otel/docs/BOUNDARY.md` Seam 2 and `otel/docs/OTEL_PLAN.md` §6, "Handed off").

### OTLP endpoint

The Collector (from Phase 1 onward) exposes:

| Protocol | Endpoint |
|---|---|
| OTLP/gRPC | `http://<collector-host>:4317` |
| OTLP/HTTP | `http://<collector-host>:4318` |

Standalone Docker Compose (`otel/docker-compose.otel.yml`): `<collector-host>`
is `localhost` from the host, or the service name `otel-collector` from
another container on the same Compose network.

In Kubernetes (`infra/helm/ai-chat/templates/otel-collector-daemonset.yaml`):
one Collector per node (docs/CONSTRAINTS.md C8), exposed via `hostPort`
4317/4318, not a ClusterIP Service — deliberately, so each pod reaches the
Collector on its *own* node rather than a load-balanced one, matching C8's
"independently deployed node Collectors, no tail sampling" design. A pod
should read its own node's IP from the Kubernetes Downward API
(`status.hostIP`) and send there, e.g.:

```yaml
env:
  - name: HOST_IP
    valueFrom: { fieldRef: { fieldPath: status.hostIP } }
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "http://$(HOST_IP):4318"
```

This is the one concrete change a future orchestrator Deployment template
would need beyond the standard env vars below.

### Standard environment variables

No custom environment variables are needed on the application side — the
standard OTel SDK environment variables are sufficient and this Collector
requires nothing non-standard of them:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://<collector-host>:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_SERVICE_NAME=backend-api        # or whatever §1 above resolves to
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
OTEL_LOGS_EXPORTER=otlp
```

`OTEL_ENABLED` (`otel/BUILD_PROMPT.md` rule 3, `docs/ACCEPTANCE.md` A3) is
an **application-side** concept, not something this workstream builds: it's
the flag the orchestrator team's own future SDK initialization would check
before calling `OTEL_EXPORTER_OTLP_ENDPOINT` at all. Nothing under `otel/`
implements or reads a variable by this name — there is nothing on the
Collector side to gate, since the Collector receives whatever arrives and
drops nothing for lack of a flag. A3 is satisfied today by construction (no
application file was changed, so `orchestrator-svc`'s test suite cannot
reference an env var that doesn't exist yet), not by a gate this workstream
added. Whoever adds the SDK owns naming this variable, defaulting it to
false, and wiring pytest's baseline run to leave it unset.

### `traceparent` across the MCP boundary

The existing correlation channel — `params._meta` on every `tools/call`
JSON-RPC request (`orchestrator-svc/app/mcp_client.py::_correlation_meta`,
read back by `mcp-server/app/logging_utils.py::ids_from_ctx`) — is exactly
where a `traceparent` (W3C Trace Context) string would ride, alongside the
four existing correlation IDs, without needing a new channel. Adding it is
one more key in the `_meta` dict on the client side and one more read on the
server side.

**Effect if it never lands:** no real parent/child spans across the process
boundary. Phase 2 filelog telemetry still works; correlation stays
`run_id`-based and is reported honestly as `correlation.confidence: medium`
(see `SEMCONV.md` §6), never silently upgraded to `high`.

### Event → span mapping table

Full table: `otel/docs/SEMCONV.md` §3. Summary of the four event groups and
their target span/operation names:

| Existing event(s) | OTel span / operation |
|---|---|
| `agent.step_started` / `step_completed` / `step_failed` | `invoke_agent` (`gen_ai.operation.name`) |
| `agent.llm_call` | `chat` |
| `agent.tool_selected` / `retried` / `tool_returned` | `execute_tool` (`agent.retried` becomes a span **event**, not its own span) |
| `mcp.request` / `response` / `error` | child spans of `execute_tool`, once trace context crosses the boundary |
| `api.request.*` | standard HTTP server spans |

Content attributes (`gen_ai.input.messages`, `gen_ai.output.messages`,
`gen_ai.system_instructions`) are **not** requested here — nothing emits them
today, and the capture-policy gate this workstream builds (Phase 3) is what
will decide whether they survive to the backend if/when the agent team adds
them. See `SEMCONV.md` §7.

### `retryable` — do not re-derive it

The MCP server already emits `retryable` as a boolean on `mcp.error`
(`mcp-server/app/tools/__init__.py`'s instrumentation wrapper). The
`[RETRYABLE]` marker inside error message text is a private wire convention
between the orchestrator and MCP server, not something either this
workstream's Collector or any future SDK instrumentation should re-parse.

---

## What this workstream needs from other teams (repeated from BOUNDARY.md, for visibility)

| Handoff | Needed from | Effect if it does not land |
|---|---|---|
| OTel SDK wired into the orchestrator, exporting OTLP | Orchestrator team | No real spans. Phase 2 filelog telemetry still works; correlation stays `run_id`-based, reported as `inferred`/`medium`. |
| `traceparent` in the existing `params._meta` channel | Orchestrator + MCP | No parent/child spans across the process boundary. |
| `route.reason_code` on routing decisions | Orchestrator team | Routing rationale stays invisible. |
| Content attributes emitted at all | Orchestrator team | Policy layer (Phase 3) still works; nothing to gate on for content until then. |
| A dashboard that renders telemetry | Frontend team | Backend UI (OpenObserve) is the fallback view. `dashboard-ui` is still the unmodified Vite starter as of this writing. |

None of these block Phases 1-5 of this workstream — all five are built (see
`otel/VERIFICATION_STATUS.md` for what's been run vs. only written).
