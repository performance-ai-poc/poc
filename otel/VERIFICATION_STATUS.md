# Verification status

`otel/BUILD_PROMPT.md`'s working method calls for verifying each phase before
starting the next. Docker Desktop's daemon was not running in the environment
this workstream was built in, so **nothing that requires Docker or a
Kubernetes cluster has been run.** Where verification was possible without
either (Helm rendering, static manifest inspection, the parts of the
application itself that don't need the Collector), it was actually run —
marked below — rather than only reviewed.

This file exists so what's actually been executed vs. only written is never
silently conflated. Update each row after actually running the corresponding
script.

| Phase | Artifact | Written | Verified live | How to verify |
|---|---|---|---|---|
| 1 | `otel/collector-config.yaml` (otlp receiver + 6 processors + otlphttp exporter) | Yes | **No** | `docker compose -f otel/docker-compose.otel.yml up -d`, then `./otel/tests/test_collector_up.sh` |
| 1 | `otel/docker-compose.otel.yml` (Collector + OpenObserve) | Yes | **No** | `docker compose -f otel/docker-compose.otel.yml config` (syntax) then `up -d` |
| 1 | `otel/tests/test_collector_up.sh` (Acceptance A1) | Yes | **No** | Run it; script itself was never executed even once |
| 1 | `otel/tests/test_redaction.sh` (Acceptance A8) | Yes | **No** | Run it after `test_collector_up.sh` passes |
| 2 | `filelog` receiver + logs pipeline addition | Yes | **No** | `otel/tests/test_filelog_ingest.sh` — requires the orchestrator running locally with stdout redirected to the path the receiver reads |
| 2 | `orchestrator-svc/pytest` unaffected by OTLP addition (A3) | Baseline captured pre-work | **Yes** — re-run three times across Phases 2-4, identical result every time: 98 passed, same 2 pre-existing unrelated failures | `cd orchestrator-svc && python -m pytest` |
| 3 | Policy layer (`otel/policy/`) | Yes | **No** | `otel/tests/test_policy_switch.sh` (also exercises `apply.sh`) |
| 4 | Collector DaemonSet + RBAC (Helm templates) | Yes | **Yes, statically** — `helm lint` and `helm template` both run clean; `otel/tests/test_rbac.sh` and `test_resources.sh` (below) both actually executed and passed against the rendered output. `kubectl apply --dry-run` needs a live cluster for API-schema discovery even in dry-run mode and could not be run; no actual `minikube`/`kubectl` deploy attempted, so live admission/enforcement is still unconfirmed. | `helm lint infra/helm/ai-chat`, `helm template demo infra/helm/ai-chat`, then a real `minikube`/`kubectl` deploy for the live half |
| 5 | `otel/tests/test_rbac.sh` (Acceptance A14) | Yes | **Yes, statically** — actually run; confirmed the rendered ClusterRole grants exactly `get`/`list`/`watch` (no forbidden verbs, no Secrets access) and both hostPath volumes are `readOnly: true`. Script also has a live `kubectl auth can-i` mode that did not run (no cluster). | `./otel/tests/test_rbac.sh` |
| 5 | `otel/tests/test_resources.sh` (Acceptance A15) | Yes | **Yes, statically** — actually run; confirmed rendered CPU/memory requests+limits (150m/500m, 224Mi/512Mi) and the queue emptyDir's 1Gi sizeLimit all fall inside C6's targets, and confirmed (informationally) that the other 5 workloads in the chart still have no resources block at all. | `./otel/tests/test_resources.sh` |
| 5 | `otel/tests/test_saturation.sh` (Acceptance A11 + A13) | Yes | **No** | Requires Docker (flood + `docker stats` + OpenObserve query) |
| 5 | `otel/tests/test_failopen.sh` (Acceptance A10 + A12) | Yes | **No** | Requires Docker (stop/start containers mid-test) |
| 5 | Collector self-monitoring (`prometheus` receiver scraping its own `:8888`, added to support A13) | Yes | **No** | Same as `test_saturation.sh` |

## What "written but not verified" means concretely

- YAML syntax has not been checked by any parser — a typo (indentation, an
  unclosed bracket in an OTTL statement string) could exist and would not be
  caught until first run.
- OTTL statement syntax (`keep_keys`, `truncate_all`, `set(...) where ...`,
  `IsMatch(...)`) was written from documented OTTL function signatures, not
  validated against the pinned Collector Contrib build
  (`otel/opentelemetry-collector-contrib:0.114.0`).
- The OpenObserve OTLP ingestion path and `_search` API shape assumed by
  `test_collector_up.sh` / `test_redaction.sh` (stream name `default`,
  SQL-style query body) was not confirmed against the pinned OpenObserve
  version (`v0.14.1`) — CONSTRAINTS.md's own warning ("wrong receiver config
  fails silently by producing no data") applies here and has not been ruled
  out.
- Helm template rendering (Phase 4) has not been run through `helm template`
  or `helm lint` even once.
- Every acceptance script exists as a file; none have exited 0 against a real
  target.
- The `filelog` receiver's `json_parser` operator (`parse_to: attributes` +
  inline `timestamp` block) was written from documented stanza operator
  syntax, not run against the pinned Collector build. If `parse_to:
  attributes` behaves differently than expected (e.g. nesting under a
  sub-key instead of flattening to top-level attributes), the transform/limits
  processor's `attributes["model_id"]`-style OTTL references would silently
  find nothing rather than erroring — exactly the "fails silently" risk
  CONSTRAINTS.md warns about.
- Phase 4's `otel-collector-configmap.yaml` filelog `container` operator is
  the single least-confirmed piece in this entire build: it's relied on to
  both unwrap the Docker/CRI container log envelope AND extract
  `k8s.namespace.name` / `k8s.pod.name` / `k8s.pod.uid` /
  `k8s.container.name` from the `/var/log/pods/...` file path, which
  `k8sattributes`' `pod_association` then keys off. This is documented
  stanza/filelog behavior, not confirmed against the pinned 0.114.0 build.
  Failure mode if wrong: records still arrive (json_parser still runs on
  whatever `body` the container operator produces), but without k8s pod
  metadata attached — degraded enrichment, not silent data loss.
- Phase 4's `kubeletstats` receiver endpoint (`${env:NODE_NAME}:10250`) and
  `auth_type: serviceAccount` assume the kubelet's read-only-adjacent
  authenticated stats port is reachable at the standard port from a pod on
  the same node with the RBAC granted in otel-collector-rbac.yaml. Not
  confirmed against a real cluster (Minikube's kubelet configuration can
  differ from a managed cluster's).
- Phase 3's capture-policy gate assumes `${env:CAPTURE_MODE}` inside an OTTL
  `where` clause is resolved to a literal string by the Collector's confmap
  loader *before* the OTTL statement is parsed (i.e. the running config
  ends up containing `where attributes["capture.mode"] != "content-approved"`
  with the literal value baked in, not a live env-var lookup at eval time).
  This is standard, documented confmap behavior, but has not been confirmed
  against the pinned 0.114.0 build — `otel/tests/test_policy_switch.sh`'s
  first real run is what actually confirms it. If it does NOT behave this
  way, `apply.sh`'s `--force-recreate` step becomes unnecessary (a live
  lookup would need no restart at all) rather than insufficient — the
  documented failure mode is "policy switch does nothing even after
  recreate," not "leaks content."

## Offline test suite (runs with no Docker, no cluster)

Added `otel/tests/test_offline_config.py` (12 tests) and
`otel/tests/test_log_contract.py` (8 tests). Run with orchestrator-svc's venv
(has PyYAML + pytest + the app on path):

```
cd orchestrator-svc
./.venv/Scripts/python.exe -m pytest ../otel/tests/test_offline_config.py ../otel/tests/test_log_contract.py -v
```

**Status: 24 passed.** These caught a real defect on first run:
`status` and `reason` (emitted on `agent.tool_returned`/`agent.retried`,
app/retry.py) were missing from the Collector's `keep_keys` allowlist and
would have been silently dropped before export — exactly the "fails silently"
class CONSTRAINTS.md warns about. Fixed in both `collector-config.yaml` and
the k8s ConfigMap; the offline suite is now the regression guard for it.

What they verify without a running Collector:
- both configs are valid YAML; the k8s ConfigMap's embedded config parses
- processor order is exactly the documented "load-bearing" order in every pipeline
- redaction delete-lists and `keep_keys` allowlists are **identical** between
  the standalone config and the k8s ConfigMap (guards the duplication drift
  risk noted below)
- credentials are deleted, content attributes are capture.mode-gated, no
  `:latest` tags, compose/chart pin the same Collector build
- **the real integration contract**: every field the unmodified app actually
  emits (driven through a live `/chat`) is either allowlisted, an envelope
  field, or a documented rename source — nothing it produces is silently
  dropped; and the app's timestamp format parses under the receiver's layout.

## What has been verified

- `orchestrator-svc`'s own test suite: 98 passed / 2 pre-existing, unrelated
  failures, identical every time it was re-run across this session
  (Acceptance A3). This confirms zero application-code impact, which is
  structural (nothing under `orchestrator-svc/` or `mcp-server/` references
  anything in `otel/`) but was also checked by actually re-running the
  suite, not assumed from the structural argument alone.
- `helm lint infra/helm/ai-chat` and `helm template demo infra/helm/ai-chat`
  (Phase 4): both run clean with no cluster required. This confirms YAML
  syntax, Helm/Sprig templating logic (including the memory_limiter
  percentage math, the checksum/config rollout-trigger annotation, and every
  `.Values.otelCollector.*` interpolation) all produce the intended output.
- `otel/tests/test_rbac.sh` (static mode) and `otel/tests/test_resources.sh`
  (static mode): both actually executed against the rendered chart output,
  both passed. Together with the two bullets above, this means everything
  in Phase 4 that *can* be checked without Docker or a cluster *has* been
  checked, by execution, not review. What remains unconfirmed is
  specifically what only a real Kubernetes API server and a real Collector
  process can confirm: that the API server accepts these manifests, that
  the `container`/`json_parser` filelog operators behave as documented
  against the pinned build, and that `kubeletstats` can actually reach the
  kubelet with the granted RBAC.

## Next action

Start Docker Desktop, then work top-to-bottom through the rows still marked
**No** above. Fix whatever the first failure reveals before moving to the
next row — the first real run is likely to surface at least one syntax or
naming issue, per CONSTRAINTS.md's own expectation that Collector config
mistakes fail silently rather than loudly. The single most likely place for
that first surprise, per the notes above, is the Phase 4 filelog `container`
operator's k8s-metadata extraction.
