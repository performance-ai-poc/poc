# ACCEPTANCE — Tests that define done

Revised against the repo snapshot. Several criteria are **already satisfied by
the existing test suite**. Those are marked so nobody rebuilds them.

Legend: ✅ already passing · 🔨 yours to build · ⚠️ depends on another team

---

## Already satisfied by the application — context, not your work

These are guarantees the application layer already provides. They are listed so
you know they exist and do not duplicate them. They are not yours to maintain.

| ID | Criterion | Existing evidence |
|---|---|---|
| ✅ E1 | Logging failure cannot fail a request | `log_event` wrapped in `try/except`, emits `logging.emit_failed` instead of raising. Both services. |
| ✅ E2 | Raw chat text never logged | `test_api.py::test_valid_chat_message_does_not_leak_into_logs` |
| ✅ E3 | Malformed body content never logged | `test_api.py::test_malformed_body_does_not_leak_raw_content_into_logs` |
| ✅ E4 | Raw rows and document text never logged | `test_mock_tools.py::test_logs_never_contain_raw_rows_or_document_text` |
| ✅ E5 | Document text never logged, only IDs | `test_mock_tools.py::test_logs_never_contain_document_text_only_ids` |
| ✅ E6 | Prompt/completion never in telemetry | `test_api_agent_telemetry.py::test_api_step_does_not_leak_raw_message_into_logs` |
| ✅ E7 | Log payload shape is stable | `test_log_event_schema.py` — flat payload, locked by regression suite |
| ✅ E8 | LLM failure degrades, does not 500 | `test_llm_failure_degrades_to_step_failed_not_500` |
| ✅ E9 | Correlation IDs propagate across the MCP boundary | `params._meta` → `ids_from_ctx`, working today |

**These nine represent the redaction and fail-open guarantees.** They exist and
they pass. Because this workstream does not modify application code, nothing you
build can break them. They are the reason the Collector's privacy processor is
defence in depth rather than the primary control.

---

## To build — MVP

### 🔨 A1. Collector receives OTLP
**Pass:** A hand-crafted OTLP span sent via `curl` appears in OpenObserve.
**Phase:** 1
**Script:** `otel/tests/test_collector_up.sh`

### 🔨 A2. Existing stdout events become queryable telemetry
**Pass:** Run one `/chat` request with **no application code change**. Every
`api.request.*` and `agent.*` event from that request appears in OpenObserve,
correlated by `run_id`.
**Phase:** 2
**Why it matters:** This alone demonstrates the pluginless claim. The
application was not modified.
**Script:** `otel/tests/test_filelog_ingest.sh`

### ✅ A3. Existing test suite unaffected by OTLP addition
**Pass:** `python -m pytest` in `orchestrator-svc/` passes with the same results
before and after the OTLP path is added, with `OTEL_ENABLED` unset.
**Phase:** N/A — guaranteed by scope
**Why it matters:** This workstream does not touch application code, so the
suite cannot regress from your work. Verify once as a baseline.
**Script:** `make test-orchestrator`

### ⚠️ A4. Real spans from the orchestrator
**Handoff — verifies the orchestrator team's instrumentation, not yours.**
**Pass:** A `/chat` request produces a root HTTP span with child `invoke_agent`
spans, correct parent/child nesting, carrying all four correlation IDs.
**Phase:** 3
**Script:** `otel/tests/test_spans.py`

### ⚠️ A5. Trace context crosses the MCP boundary
**Handoff — same.**
**Pass:** One trace in OpenObserve spans orchestrator → MCP server → tool, with
genuine parent/child relationships rather than matched IDs.
**Phase:** 4
**Script:** `otel/tests/test_distributed_trace.py`

### ⚠️ A6. `trace_id` still absent from stdout log lines
**Handoff — a constraint on their work, verified here.**
**Pass:** `mcp-server/tests/test_mock_tools.py`'s `assert "trace_id" not in ln`
still passes after Phase 4.
**Phase:** 4
**Why it matters:** Trace IDs belong in OTLP spans, not in the JSON log
payload. Do not weaken this test to make Phase 4 easier.
**Script:** existing suite

### ⚠️ A7. GenAI attributes present
**Handoff — depends on their span minting.**
**Pass:** `chat` spans carry `gen_ai.request.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`. `execute_tool` spans
carry `gen_ai.tool.name`.
**Phase:** 5
**Script:** `otel/tests/test_semconv.py`

### 🔨 A8. Collector-level redaction
**Pass:** Seed these exact values into a request and assert none reach the
backend:
```
API key:       SEEDED_APIKEY_d41d8cd98f00b204
Email:         seeded.user@example-test.invalid
Auth header:   Bearer SEEDED_TOKEN_9e107d9d372bb682
Unknown attr:  x-seeded-unexpected-header
```
The unknown attribute tests allowlist-vs-denylist: it is named nowhere in the
config, so a denylist would let it through.
**Phase:** 1, verified continuously
**Note:** This is defence in depth. The emitters already guarantee E2–E6.
**Script:** `otel/tests/test_redaction.sh`

### 🔨 A9. Policy switch changes what arrives
**Pass:** Changing `capture.mode` from `metadata-only` to `content-approved`
visibly changes what reaches the backend, with no application restart.
**Phase:** 3
**Blocked on:** orchestrator team emitting content attributes at all
**Script:** `otel/tests/test_policy_switch.sh`

### 🔨 A10. Collector shutdown — the demo test
**Pass:** Stop the Collector mid-session. The application continues serving
`/chat` requests with **zero** failures. stdout logging continues uninterrupted.
Restart the Collector; collection resumes with no application restart.
**Phase:** 2 onward
**Why it matters:** This is demo scenario 4. It is close to free here — the
stdout path was never coupled to the Collector.
**Script:** `otel/tests/test_failopen.sh`

### 🔨 A11. Collector saturation
**Pass:** Under telemetry flood the Collector drops records and stays inside its
configured CPU and memory ceilings. Application latency and availability
unaffected.
**Phase:** 4
**Script:** `otel/tests/test_saturation.sh`

### 🔨 A12. Backend outage
**Pass:** Stop OpenObserve. The export queue fills to its bound then drops. No
unbounded disk growth. Application unaffected. Restart; export resumes.
**Phase:** 1 onward
**Script:** `otel/tests/test_failopen.sh`

### 🔨 A13. Telemetry-loss visibility
**Pass:** Dropped-record counts and queue-saturation metrics are queryable. The
system reports its own data loss rather than hiding it.
**Phase:** 4
**Script:** `otel/tests/test_saturation.sh`

### 🔨 A14. Read-only cluster access
**Pass:** The Collector's ServiceAccount has `get`, `list`, `watch` only. No
`create`, `update`, `patch`, `delete`, `exec`. No Secrets access. All host
mounts `readOnly: true`.
**Phase:** 4
**Note:** The chart currently has **no RBAC at all** for any workload. The
Collector will be the first thing in it with a defined permission boundary.
**Script:** `otel/tests/test_rbac.sh`

### 🔨 A15. Resource ceilings
**Pass:** The Collector declares and stays inside CPU, memory, and disk limits.

| Resource | Target |
|---|---|
| CPU request | 100–200m |
| CPU limit | 500m |
| Memory request | 192–256Mi |
| Memory limit | 512Mi |
| Persistent queue | 256Mi–1Gi |

**Phase:** 4
**Note:** No application workload in the chart has resource limits today. The
Collector should not follow that precedent.
**Script:** `otel/tests/test_resources.sh`

---

## Blocked on other teams

| ID | Criterion | Blocked on |
|---|---|---|
| ⚠️ B1 | `route.reason_code` present on `invoke_agent` spans | Orchestrator team defining the enum and emitting it |
| ⚠️ B2 | Dashboard renders `capture.mode` and `correlation.confidence` | Frontend team — `dashboard-ui` is still the Vite starter |
| ⚠️ B3 | Inferred relationships visually distinct from observed | Frontend team, same |
| ⚠️ B4 | DB Agent produces agent-level telemetry | Orchestrator team — currently a stub returning a canned string |

None of these are this workstream's to build or chase. They are listed so the
dependency is visible when a demo scenario depends on one.

---

## The four demo scenarios

| # | Scenario | Depends on | Notes |
|---|---|---|---|
| 1 | Normal interaction captured end to end | A2, A4, A5 | Needs `AGENT_LIVE_CALLS=true` for a realistic trace |
| 2 | A failure identified | A4, existing `agent.step_failed` | `FORCE_FAILURE_TRIGGER` produces this deterministically — use it deliberately |
| 3 | Customer changes capture policy | A9 | Blocked on content attributes existing at all |
| 4 | Collector stopped, app keeps running | A10 | The one that wins the room. Rehearse until boring. |

Scenario 2 has a shortcut already in the code: the magic strings
`__FORCE_API_AGENT_FAILURE__` and `__FORCE_DB_AGENT_FAILURE__` deterministically
force a step failure. They run ungated in production paths, which is a mild risk
in general but exactly what you want for a reproducible demo.

Scenario 3 is the one most likely to slip, because it depends on content
attributes that nothing currently emits. If it is at risk, the fallback is to
demonstrate the policy switching a *metadata* field on and off instead — less
dramatic, same architectural point.
