# Testing

## Offline (no Docker or cluster)

Run from `orchestrator-svc/` (its venv has the app, PyYAML, and pytest):

```bash
./.venv/Scripts/python.exe -m pytest \
  ../otel/tests/test_offline_config.py \
  ../otel/tests/test_span_contract.py \
  ../otel/tests/test_log_contract.py \
  ../otel/tests/test_trace_propagation.py
```

These check config validity and processor order, that the standalone config and
the k8s ConfigMap agree on redaction and the allowlist, that every field the app
actually emits (spans and logs) is either allowlisted or intentionally dropped
with nothing sensitive surviving, and that trace context round-trips across the
MCP boundary. `test_rbac.sh` and `test_resources.sh` run static checks against
the rendered chart. The application's own suite (`orchestrator-svc`) is
unaffected by the telemetry work.

## Live (Docker Compose)

```bash
docker compose up --build -d
```

The scripts that need the running stack: `test_collector_up.sh`,
`test_redaction.sh`, `test_filelog_ingest.sh`, `test_policy_switch.sh`,
`test_failopen.sh`, `test_saturation.sh`. OpenObserve is at
http://localhost:5080.

## Not yet exercised

The Kubernetes-specific paths — the DaemonSet reading host metrics and kubelet
stats, and the pod-log filelog on a real node — have only been validated by
rendering the chart, not by a live Minikube deploy. The distributed trace across
the MCP boundary is unit-tested and runs live in offline agent mode; seeing the
spans nest inside a running MCP server needs `AGENT_LIVE_CALLS=true` and a
reachable model endpoint.
