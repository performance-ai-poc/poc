# OTEL_PLAN — What exists, what's missing, what to build

Written against the repository snapshot in `REPO_STATE.md`. Supersedes the
earlier greenfield build kit, which assumed an empty repo.

---

## 1. The headline

The application layer is built and working. The telemetry transport layer does
not exist. But the hard part of telemetry — correlation, fail-open discipline,
and metadata-only redaction — **is already implemented and tested**, just not
under the OpenTelemetry name.

`otel/` contains `.gitkeep` and a README. That is the entire workstream,
unstarted since the initial monorepo commit.

This inverts the plan. The job is not "build a telemetry system." It is **"add
OTLP as a second output path alongside stdout logging that already works."**

---

## 2. What the repo already satisfies

Read against the constraints in the earlier kit:

| Constraint | Status | Evidence in repo |
|---|---|---|
| **C1 off-path** | ✅ Satisfied | No proxy, no gateway. Loggers write to stdout only, never a network socket. |
| **C2 fail-open** | ✅ At the emitter | `log_event` is wrapped in `try/except` that can never propagate; emits `logging.emit_failed` instead of raising. Stated as the module's own design principle. |
| **C3 metadata-only** | ✅ Satisfied, tested | Raw prompts, SQL, rows, document text never logged. Only SHA-256 digests (`args_digest`, `instruction_digest`) and an allowlist of result keys. |
| **C4 allowlist** | ✅ Satisfied | `_SAFE_RESULT_METADATA_KEYS` in `retry.py`, `_RESPONSE_META_KEYS` in `mcp-server/app/tools/__init__.py`. Allowlist, not denylist. |
| **C5 read-only host/RBAC** | ❌ N/A yet | No collector to grant anything to. Also: chart has **no RBAC at all** for any workload. |
| **C6 bounded resources** | ❌ Missing | No resource requests/limits on any application Deployment. Only `postgresql.resources`, defaulting to `{}`. |
| **C7 platform agnostic** | ⚠️ Partial | Event vocabulary is custom and portable. But LangGraph and FastMCP are structurally embedded. |
| **C8 no tail sampling** | ✅ Trivially | No sampling of any kind exists. |
| **C9 pin versions** | ✅ For apps | Python deps pinned in `requirements.txt`. No collector to pin yet. |
| **C10 no remediation** | ✅ Satisfied | Nothing writes back to the observed system. |

**Four of ten already met, two by passing tests.** This is a codebase that was
designed for telemetry to arrive later. `orchestrator-svc/app/context.py` says
so directly: a real OTel trace_id "will be minted by the SDK once tracing
instrumentation is wired in."

---

## 3. The correlation system that already exists

Four IDs are threaded manually through the whole request lifecycle:

| ID | Minted | Caller-suppliable | Role |
|---|---|---|---|
| `run_id` | `app/context.py::resolve_context` | No | End-to-end business correlation |
| `request_id` | same | No | Per-HTTP-request |
| `session_id` | same, falls back to fresh UUID | Yes | Conversation grouping |
| `tenant_id` | same, falls back to `default-tenant` | Yes | Tenancy |

Propagation path, already working:

1. `RequestContextMiddleware` mints them once per request
2. Passed **explicitly as function arguments** to every downstream call — a
   deliberate choice over `contextvars`, documented in `context.py`
3. `mcp_client.py::_call_tool_live` puts them in JSON-RPC `params._meta`
4. MCP server's `ids_from_ctx` reads them back off `ctx.request_context.meta`

**This is a working distributed correlation system.** It is not W3C Trace
Context, and both loggers deliberately never emit `trace_id` — there is a test
asserting its absence (`assert "trace_id" not in ln`).

The upgrade path is therefore narrow and clear: mint real trace and span IDs,
carry them alongside the four existing IDs, and keep the existing IDs as
business-level attributes. Nothing gets removed.

---

## 4. Scope of this workstream

**Telemetry infrastructure only.** Everything under `otel/`, the Collector's
Helm templates, its RBAC and resource limits, and the telemetry backend.

**Not this workstream:** application code in `orchestrator-svc/` or
`mcp-server/`. Instrumenting those services with an OTel SDK belongs to the
orchestrator team. See `BOUNDARY.md`.

The governing principle is **additive only**: nothing built here requires an
edit to an application file. This is not a reduced scope. Because both services
already write structured JSON to stdout with four correlation IDs threaded end
to end, reading that stream produces a complete telemetry plane with no change
from anyone.

It also makes fail-open close to free. If the Collector stops, stdout logging
continues untouched, because the two paths were never coupled.

## 5. The naming conflict — open, not ours alone

`orchestrator-svc/README.md` flags an unresolved discrepancy between two
governing documents. It is recorded here because it affects the mapping spec,
**not because this workstream decides it.**

| Question | Implemented | Other document |
|---|---|---|
| Orchestrator `service.name` | `backend-api` | `agent-orchestrator` |
| Tool-selection event | `agent.tool_selected` | `agent.tool.selected` |

**Status: open, to be settled jointly with the orchestrator team.**

Neither answer affects the architecture. Build so either is a one-line change:
keep every name in a single mapping table rather than scattering literals across
the collector config and tests. Until it lands, mirror what the code emits.

## 6. Build phases

Each phase ends with something runnable and demonstrable. Do not start a phase
before the previous one runs.

### Phase 0 — Record open items (no code)
- Note the naming conflict as open; do not resolve it unilaterally
- Note that `route.reason_code` needs an enum from the orchestrator team
- Write both into `otel/HANDOFF.md` as items awaiting a joint decision

### Phase 1 — Collector, standalone
Get a Collector running and receiving *something* before touching application
code.

- `otel/collector-config.yaml` — receivers `otlp`, `filelog`; processors
  `memory_limiter`, `resource`, `attributes/privacy`, `transform/limits`,
  `filter/noise`, `batch`; exporter `otlphttp`
- `otel/docker-compose.otel.yml` — Collector + OpenObserve, standalone
- Verify: send a hand-crafted OTLP span with `curl`, see it in OpenObserve

**Do not add `k8sattributes` or `kubeletstats` yet.** Those need the Kubernetes
path and will fail confusingly outside it.

### Phase 2 — Filelog first, zero application changes
The single highest-value, lowest-risk step. Both services already emit
structured JSON to stdout. Point the Collector at the container log path and
every existing event becomes queryable telemetry with no code change at all.

- Add `filelog` receiver reading `/var/log/pods/*/*/*.log`
- Parse the existing JSON shape; promote `run_id`, `request_id`, `session_id`,
  `tenant_id`, `service.name`, `event` to attributes
- Verify: run a `/chat` request, see `api.request.started` and every `agent.*`
  event land in OpenObserve, correlated by `run_id`

**This alone demonstrates the pluginless claim.** The application was not
modified. That is worth showing on its own before anything else is built.

### Phase 3 — Policy layer
`otel/policy/` with a `capture.mode` switch. The repo currently has **no** gate
on content capture — exclusion is unconditional and hardcoded. Adding the policy
layer is what makes demo scenario 3 possible.

- `metadata-only` (default): current behaviour exactly
- `content-approved`: permits `gen_ai.input.messages` / `gen_ai.output.messages`
- Switching the policy must not require an application restart

### Phase 4 — Kubernetes path
- Collector DaemonSet in `infra/helm/ai-chat/templates/`
- Add `k8sattributes` and `kubeletstats` receivers
- **RBAC, which the chart currently has none of** — ServiceAccount, ClusterRole
  with `get`/`list`/`watch` only
- Read-only host mounts
- Resource requests/limits on the Collector

### Phase 5 — Acceptance tests
See `ACCEPTANCE.md`. Several are already satisfied by the existing suite; those
are marked so you do not rebuild them.

---


### Handed off — not built here

Application instrumentation: OTel SDK in `orchestrator-svc/`, span minting in
the middleware and graph nodes, `traceparent` in the existing `params._meta`
channel. **Orchestrator team's work.**

Your `otlp` receiver from Phase 1 already accepts it whenever they wire it up.
No further work on this side.

Consequence if it never lands: no real parent/child spans. Phase 2 telemetry
still works, correlation stays `run_id`-based, and it is reported honestly as
`inferred`. The demo is less impressive and still true.

## 7. Other teams' work that affects your demo

Awareness only. None are yours to fix or to chase.

| Item | Owner | Why it matters to you |
|---|---|---|
| `dashboard-ui` is the unmodified Vite starter — counter button, React logos | Frontend | Nothing renders your telemetry. Their half of the demo does not exist. |
| DB Agent is a stub returning a canned string | Agent team | It emits no `agent.llm_call`, `tool_selected`, or `tool_returned`. Only the REST API Agent path produces interesting telemetry. |
| `AGENT_LIVE_CALLS` defaults false everywhere | Agent team | Good for building against. But the demo needs it true, and `LLM_BASE_URL` defaults to an ASU-internal host that may need VPN. |
| `route.reason_code` enum undefined | Agent team | Your semconv mapping needs it. |
| No resource limits on any workload | Infra | Your Collector will have them. Nothing else does. |

---

## 8. Risks specific to this plan

**The offline-mode data mirror.** `orchestrator-svc/app/mcp_client.py` keeps
`_OFFLINE_ENDPOINTS` and `_OFFLINE_SHIPMENTS` as hardcoded copies of the MCP
server's seed data. Nothing enforces they stay in sync. If you build and demo
in offline mode, then switch to live for the actual presentation, the data may
differ. Test the demo in the mode you will present in.

**`FORCE_FAILURE_TRIGGER` runs in production code paths.** The magic strings
`__FORCE_API_AGENT_FAILURE__` and `__FORCE_DB_AGENT_FAILURE__` are ungated.
This is useful — it is the cleanest way to produce demo scenario 2, a failure
captured end to end. Use it deliberately rather than discovering it by accident.

**The `[RETRYABLE]` marker is a private string convention**, not a standard. It
travels inside error message text because FastMCP collapses exceptions to
strings. Your telemetry should surface `retryable` as a boolean attribute, which
the MCP server already emits on `mcp.error`. Do not re-derive it by string
matching in the Collector.

**No CI exists.** Nothing runs the test suite automatically. Also worth knowing:
the MCP server suite *silently skips* rather than fails when Postgres is
unreachable, so a naive CI run would report success having executed nothing.
