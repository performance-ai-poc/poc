# SEMCONV — Mapping the existing event vocabulary onto OTel

This replaces the greenfield version. The repo already emits a complete,
tested, custom event vocabulary. This document maps it onto OpenTelemetry
rather than asking anyone to rewrite it.

---

## 1. Why OTLP and not a framework SDK

The team raised Langfuse, LangSmith, and Phoenix. All three are built around
specific agent environments; a capture layer built on one inherits that
boundary.

The OpenTelemetry GenAI semantic conventions define fixed span names and
attribute keys so an LLM call looks the same regardless of which framework
emitted it. Standardizing on them means the Collector can fan out to Langfuse,
Tempo, Phoenix, or Grafana by adding an exporter, changing nothing upstream.

This matters more here than in a typical project, because the orchestrator is
built directly against LangGraph's `StateGraph` API. The orchestration is
framework-coupled by design. Keeping the *telemetry* framework-neutral is what
lets the team say the POC runs on LangGraph but extends to other agent stacks.

**Stability caveat, state it plainly.** As of the v1.42.0 semantic-conventions
release in June 2026, `gen_ai.*` attributes moved to a dedicated GenAI
conventions repository. That is an organizational change for release cadence,
**not** a graduation to stable. Names can still change between versions. Pin the
convention version and keep the mapping in one module so a rename is a small
edit.

---

## 2. Naming conflict — OPEN, decided jointly

`orchestrator-svc/README.md` flags an unresolved discrepancy between two
governing documents. **This is not the OTel workstream's call to make alone.**
It is recorded here because it blocks the mapping module, not because it is
decided.

| Question | Implemented in code | Other document's example |
|---|---|---|
| Orchestrator `service.name` | `backend-api` | `agent-orchestrator` |
| Tool-selection event | `agent.tool_selected` | `agent.tool.selected` |

**Status: open. To be settled with the orchestrator team.**

Neither answer affects the architecture. OTel does not constrain `service.name`
to any particular scheme, and the event-name difference is cosmetic.

**Build so either answer is a one-line change.** Keep every name in a single
mapping table in one module. Do not scatter string literals across the
collector config, the span builders, and the tests. When the decision lands, it
should be one edit in one place.

Until it lands, mirror whatever the code currently emits, so nothing breaks
either way.

---

## 3. Event → span mapping

The existing events become spans. The existing attributes become span
attributes. Nothing is renamed at the emitter; the mapping happens where OTLP
spans are minted.

### `agent.step_started` / `step_completed` / `step_failed` → `invoke_agent`

| Existing attribute | OTel attribute | Notes |
|---|---|---|
| `agent` | `gen_ai.agent.name` | Already low-cardinality |
| `graph.node` | `graph.node` | Keep as-is, project namespace |
| `step.sequence` | `step.sequence` | Keep as-is |
| `instruction_digest` | `instruction_digest` | Keep. SHA-256, never raw text |
| `duration_ms` | span duration | Native, not an attribute |
| `error_type` | `error.type` | On failure only |
| — | `gen_ai.operation.name` = `invoke_agent` | Add |
| — | `route.reason_code` | **Not yet emitted.** See §5 |

### `agent.llm_call` → `chat`

This event is already almost a complete `chat` span.

| Existing attribute | OTel attribute |
|---|---|
| `model_id` | `gen_ai.request.model` |
| `input_tokens` | `gen_ai.usage.input_tokens` |
| `output_tokens` | `gen_ai.usage.output_tokens` |
| `latency_ms` | span duration |
| `call.sequence` | `call.sequence` (keep) |
| — | `gen_ai.operation.name` = `chat` |
| — | `gen_ai.provider.name` — derive from `LLM_BASE_URL` |

Token counts come from `usage.prompt_tokens` / `usage.completion_tokens` in the
OpenAI-compatible response, which `app/llm.py` already reads. Do not estimate
from byte counts.

### `agent.tool_selected` / `retried` / `tool_returned` → `execute_tool`

| Existing attribute | OTel attribute |
|---|---|
| `tool_name` | `gen_ai.tool.name` |
| `args_digest` | `args_digest` (keep — SHA-256, never raw args) |
| `attempt` / `retry.attempt` | `attempt` (keep) |
| `latency_ms` | span duration |
| `status_code`, `row_count`, `exec_ms`, `count`, `endpoint_count` | keep as-is |
| `retrieval_ids` | keep as-is |
| — | `gen_ai.operation.name` = `execute_tool` |
| — | `gen_ai.tool.type` = `mcp` |

`agent.retried` becomes a span event on the `execute_tool` span, not its own
span. It is a retry of the same logical operation.

### `mcp.request` / `mcp.response` / `mcp.error` → child spans

Server-side spans, parented to the orchestrator's `execute_tool` span once
Phase 4 lands trace context propagation.

| Existing attribute | OTel attribute |
|---|---|
| `tool` | `gen_ai.tool.name` |
| `args_digest` | `args_digest` |
| `duration_ms` | span duration |
| `retryable` | `retryable` (keep — boolean, already emitted) |
| `error_type` | `error.type` |

**Do not re-derive `retryable` by string-matching `[RETRYABLE]` in the
Collector.** The MCP server already emits it as a boolean. The string marker is
an internal convention between the two services, not something telemetry should
parse.

### `api.request.*` → HTTP server spans

Standard HTTP semantic conventions, not GenAI. `endpoint`, `status_code`,
`duration_ms` map onto `http.route`, `http.response.status_code`, span duration.

---

## 4. Attributes on every span

The four correlation IDs stay. They are business-level identifiers and remain
useful even once real trace IDs exist.

| Attribute | Source | Always present |
|---|---|---|
| `run_id` | `RequestContext` | Yes |
| `request_id` | `RequestContext` | Yes |
| `session_id` | `RequestContext` | Yes |
| `tenant_id` | `RequestContext` | Yes |
| `service.name` | Module constant | Yes |
| `capture.mode` | Policy (Phase 6) | Yes, once policy lands |
| `correlation.confidence` | Derived, see §6 | Yes |

---

## 5. `route.reason_code` — not yet emitted

The routing decision is made by deterministic keyword matching in
`app/orchestrator/routing.py::pick_agent`. Which rule fired is currently not
captured anywhere.

This is the single most valuable attribute the agent team could add, because it
is the one thing passive collection genuinely cannot see. The routing rationale
exists only inside the orchestrator's own logic.

Suggested enum — agent team owns the final list:

```
DOCUMENT_INTENT
SUPPORT_INTENT
COMPLIANCE_INTENT
API_INTENT
FALLBACK_DEFAULT
```

Enum only. Never free text, never model reasoning. It attaches to the
`invoke_agent` span.

---

## 6. Correlation confidence

The repo currently achieves correlation by matching `run_id` across two
independent stdout streams. That is a real relationship but it is not an
observed trace, and the distinction must survive into the UI.

| Confidence | Condition | When it applies here |
|---|---|---|
| `high` | Real trace context propagated end to end | After Phase 4 |
| `medium` | Stable `run_id` present in all records | Today, via Phase 2 filelog |
| `low` | Endpoint, pod, and timing only | If IDs are missing |
| `none` | No identifiers | Should not occur in this stack |

Anything below `high` carries `correlation.confidence=inferred`.

The collection plane's obligation ends at stamping the attribute accurately on
every record. How it is displayed is the frontend team's decision. Stamping it
correctly is what makes an honest display possible; nothing here dictates one.

---

## 7. Content capture

The repo currently **never** captures prompt or completion text. The exclusion
is unconditional and hardcoded into what the emitter functions accept — there is
no `LOG_PROMPTS` flag.

Phase 6 adds the policy gate. Until then:

| Attribute | Status |
|---|---|
| `gen_ai.input.messages` | Not emitted. Requires policy + agent-team change |
| `gen_ai.output.messages` | Not emitted. Same |
| `gen_ai.system_instructions` | Not emitted. Same |

When the policy layer lands, these are deleted by the Collector's
`attributes/privacy` processor unless `capture.mode: content-approved` is
active. The agent team may emit them; the Collector decides whether they
survive. That separation is the point — the customer controls exposure through
policy, not through a code change in the application.

---

## 8. Never emit

| Do not emit | Emit instead | Already enforced? |
|---|---|---|
| Raw chat message text | `instruction_digest` | Yes, tested |
| Raw MCP tool arguments | `args_digest` | Yes, tested |
| Raw SQL, rows, document text | Counts, IDs, durations | Yes, tested |
| Chain-of-thought | `route.reason_code` enum | Yes, nothing emits it |
| API keys, tokens, auth headers | Nothing | No collector filter yet |
| Prompt text as a **metric label** | Nothing | No metrics pipeline yet |
| Estimated token counts | Omit the attribute | Yes, reads real `usage` fields |

The first four are already guaranteed by passing tests. The Collector's privacy
processor is defence in depth for the remaining three, not the primary control.
