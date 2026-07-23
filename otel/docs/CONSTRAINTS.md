# CONSTRAINTS — Rules that fail the build if violated

Each rule states the rule, why it exists, and how it is verified. If an
implementation choice conflicts with one of these, the implementation changes.

## Status against the current repo

Four of these are already satisfied by the existing application code, two of
them enforced by passing tests. Treat those as constraints on your work rather
than as things to build.

| Rule | Status | Where |
|---|---|---|
| C1 off-path | Satisfied | No proxy or gateway anywhere; loggers are stdout-only |
| C2 fail-open | Satisfied at the emitter | `log_event` try/except in both services |
| C3 metadata-only | Satisfied, tested | Digests + allowlists; six passing tests |
| C4 allowlist | Satisfied | `_SAFE_RESULT_METADATA_KEYS`, `_RESPONSE_META_KEYS` |
| C5 read-only access | To build | Chart has no RBAC for any workload today |
| C6 bounded resources | To build | No workload in the chart has resource limits |
| C7 platform agnostic | Partial | Vocabulary is portable; LangGraph/FastMCP are embedded |
| C8 no tail sampling | Trivially satisfied | No sampling exists |
| C9 pin versions | Satisfied for apps | Python deps pinned; no collector yet |
| C10 no remediation | Satisfied | Nothing writes back to the observed system |

---

## C1. Off-path only

**Rule.** No component in this repo may sit in the production request path. No
proxy, no in-path gateway, no interception of model traffic.

**Why.** The monitoring plane must remain off-path so that no telemetry failure
can become a customer-facing failure. An earlier design considered an egress
gateway for content capture; it is explicitly excluded from this POC and
remains a separate future tier requiring customer approval.

**Verified by.** Code review plus `tests/test_failopen.sh`. If any application
environment variable points at a Collector endpoint as a request destination
rather than a telemetry destination, this rule is violated.

---

## C2. Fail-open by construction

**Rule.** Stopping any or all components in this repo produces a telemetry gap,
never a failed customer request.

Mechanically:
- No application endpoint points to the Collector as a dependency
- No synchronous call from production code to telemetry
- Collector readiness cannot gate application readiness
- Backend or dashboard availability is irrelevant to application health
- Export queues are bounded and drop when full
- No unbounded retry, no unbounded disk growth
- Removing the Collector requires no application restart

**Why.** This is the product's central promise and the thing the demo is
designed to prove in scenario 4.

**Verified by.** `tests/test_failopen.sh`, `tests/test_saturation.sh`.

---

## C3. Metadata-only by default

**Rule.** Prompts, completions, retrieved documents, tool parameters,
authorization headers, cookies, secrets, and internal context are excluded
unless a named policy explicitly enables them.

Specifically these attributes are deleted in the default pipeline:
```
gen_ai.input.messages
gen_ai.output.messages
gen_ai.system_instructions
http.request.header.authorization
http.request.header.cookie
api_key
```
And these are hashed rather than passed through:
```
user.email
user.id
session.id
```

**Why.** OpenTelemetry itself recommends that sensitive GenAI payloads should
not be captured automatically. Default-deny with an explicit allowlist is the
only posture that survives a customer security review.

**Verified by.** `tests/test_redaction.sh` seeds known sensitive values and
asserts they never appear in the backend.

---

## C4. Allowlist, never denylist

**Rule.** Header and attribute handling uses an allowlist. Anything not
explicitly permitted is dropped.

**Why.** A denylist fails open on anything the author did not anticipate, which
is exactly the class of field most likely to carry a surprise.

**Verified by.** Config review plus a redaction test that seeds an attribute
name not mentioned anywhere in the config and asserts it does not arrive.

---

## C5. Read-only host and cluster access

**Rule.**
- Every host mount is `readOnly: true`
- Container runs non-root, `allowPrivilegeEscalation: false`, all capabilities dropped
- RBAC grants `get`, `list`, `watch` on pods, namespaces, nodes only
- No `create`, `update`, `patch`, `delete`, `exec`
- No Secrets API permission
- No unrestricted Docker socket access
- Mount the narrowest paths that yield the required data

Read-only mounts required:
```
/var/log/pods
/var/log/containers
selected portions of /proc
selected portions of /sys
/etc/hostname
/etc/machine-id
```
Writable and bounded:
```
/var/lib/otelcol      # file offsets and persistent queue only
```

**Why.** A writable host log mount is a documented container-escape vector: the
kubelet serves that directory over an HTTP file server that follows symlinks,
so a writable mount plus root can be turned into host access. Read-only,
non-root, narrowest-path removes the vector.

Note also that the Restricted Pod Security Standard forbids hostPath volumes
outright. This tier is the lowest-risk option available, not a zero-risk one,
and the demo should describe it that way.

**Verified by.** Manifest review; `tests/` asserts no write-capable mount exists
outside `/var/lib/otelcol`.

---

## C6. Bounded resources

**Rule.** The Collector declares explicit ceilings and stays inside them.

Starting targets per node, to be validated rather than assumed:

| Resource | Target |
|---|---|
| CPU request | 100–200 millicores |
| CPU limit | 500 millicores |
| Memory request | 192–256 MiB |
| Memory limit | 512 MiB |
| Persistent queue | 256 MiB–1 GiB |
| Metric interval | 15–30 seconds |
| Export batch interval | 2–5 seconds |
| App p95 latency change | ≤ 2% under controlled load |
| App error-rate change | 0 attributable errors |

**Why.** "Lightweight" is meaningless without numbers. These are acceptance
targets, not product guarantees, and the difference matters when talking to a
customer.

**Verified by.** `tests/test_saturation.sh` plus container resource inspection.

---

## C7. Platform agnostic

**Rule.** No dependency on any agent framework. No LangChain-specific,
CrewAI-specific, or vendor-specific collection path.

The Collector accepts OTLP. Anything that speaks OTLP works. The POC may
demonstrate with one framework, but nothing in this repo may assume it.

**Why.** Framework-coupled observability tools only work inside their own
ecosystem. Standardizing on OTLP means the same telemetry can be forwarded to
Grafana, Tempo, Phoenix, Langfuse, or any other OTLP-compatible backend by
adding an exporter, without changing anything upstream.

**Verified by.** Grep the repo for framework names; there should be none outside
documentation and the mock emitter used for testing.

---

## C8. No tail sampling in node Collectors

**Rule.** Do not configure the tail sampling processor in an independently
deployed node Collector.

**Why.** Tail sampling requires all spans for a trace to reach the same
Collector instance. Independent node Collectors each see a fraction of any
distributed trace, so tail sampling there produces silently wrong results.

If trace-aware sampling is needed later, add a centralized gateway Collector
that all node Collectors export to. That is a deliberate future addition, not
part of the MVP.

**Verified by.** Config review.

---

## C9. Pin the Collector version

**Rule.** Use an explicit image tag. Never `latest`.

**Why.** Recent Collector Contrib releases are migrating several component
names to snake_case while retaining deprecated aliases. A floating tag will
break the config at an unpredictable moment, most likely during a demo.

**Verified by.** `scripts/validate-config.sh` runs the pinned binary against the
config file.

---

## C10. No automated remediation

**Rule.** The collection plane observes. It never acts on the observed system.
No restarts, no config changes, no traffic manipulation, no scaling decisions.

**Why.** Read-only is the trust story. Any write capability, however
well-intentioned, converts a monitoring vendor into an operational risk.

**Verified by.** RBAC review; the absence of any write verb makes this
structurally enforced rather than merely promised.
