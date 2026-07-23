# Verification status — combined observability branch

This branch merges the app-side OTel SDK instrumentation with the hardened
collection plane. Docker/Kubernetes were not available in the environment it was
assembled in, so live collector/cluster behavior is unverified. Everything that
could be checked without them **was actually run** (not just reviewed) — marked
below.

## Verified by execution

| What | Result |
|---|---|
| App test suite (`orchestrator-svc`, full) | **172 passed, 8 failed** — the 8 are pre-existing on `main` (reply-text drift from the real DB agent + LLM summarization); confirmed identical on clean `origin/main`. The OTel instrumentation and the merge introduce **zero** regressions. |
| Merge-blocking bug found + fixed | Their branch's merge of `main` left a duplicate `db_agent_node` (`await` in a sync def) that blocked the entire app from importing. Fixed. |
| `test_offline_config.py` (17) | Config validity, exact processor order, redaction/allowlist **parity** between the standalone config and the k8s ConfigMap, capture-mode gating, pinned images. |
| `test_span_contract.py` (5) | Captures the app's **real spans** in-memory and proves every attribute is allowlisted / normalized / intentionally-deleted (nothing silently dropped), gen_ai semconv present + kept, raw-SQL keys deleted not allowlisted. Caught the old-vs-new HTTP semconv mismatch. |
| `test_log_contract.py` (7) | Same guarantee for the stdout/filelog path. |
| `test_trace_propagation.py` (4) | Drives BOTH real functions (orchestrator `_correlation_meta` inject → `_meta` → mcp `extract_context`) and asserts the `mcp.tool` span parents onto the orchestrator's `execute_tool` span (one trace). |
| `test_rbac.sh`, `test_resources.sh` (static) | RBAC is get/list/watch only, no Secrets; all host mounts read-only; resource ceilings within C6. |
| `helm lint` + `helm template` | Clean. Memory math, checksum annotation, OpenObserve wiring, hostIP endpoint, secret derivation all render correctly. |
| `docker compose config` (root + otel) | Both compose files are valid. |

## NOT verified (needs Docker / a cluster)

- The Collector process actually loading `collector-config.yaml` on the pinned
  build (`0.153.0`) and the OTTL statements executing as written. First live run
  is where a syntax/semantic surprise would show; the offline tests validate
  structure and the app-facing contract, not the running Collector.
- **Timestamp layout** for filelog (`%Y-%m-%dT%H:%M:%S.%f%z`): the app emits a
  colon offset (`+00:00`); stanza's Go `%z` may reject it. Highest-risk silent
  failure on the filelog path (the OTLP path is unaffected). Left as-is per
  prior decision; verify on first live run.
- The filelog `container` operator's k8s-metadata extraction, `hostmetrics`
  reading host `/proc`+`/sys`, and `kubeletstats` reaching the kubelet — none
  run outside a real node.
- **Live distributed trace nesting** inside a running MCP server (does FastMCP
  pass `_meta` through, does the span visibly nest in OpenObserve). The
  round-trip *logic* is unit-tested; the end-to-end nesting is not.
- OpenObserve actually ingesting and displaying the OTLP, and the redaction /
  policy-switch / fail-open / saturation scripts (all need the stack up).

## First-live-run checklist

1. `make dev` (or `docker compose up --build`), confirm all pods/containers healthy.
2. `./otel/tests/test_collector_up.sh` then `test_redaction.sh` — Collector receives + redacts.
3. Drive a `/chat` (AGENT_LIVE_CALLS=true for a real trace), open OpenObserve, confirm one trace spans orchestrator → MCP → tool with gen_ai attributes and NO raw SQL / prompt text.
4. `./otel/policy/apply.sh content-approved` and re-check that content now appears; restore `metadata-only`.
5. `./otel/tests/test_failopen.sh` — stop the Collector mid-session, app keeps serving.
6. If filelog logs don't appear, the timestamp layout is the first suspect (above).
