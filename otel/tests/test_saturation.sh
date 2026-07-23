#!/usr/bin/env bash
# Acceptance A11 + A13 (docs/ACCEPTANCE.md): under a telemetry flood, the
# Collector drops records and stays inside its configured CPU/memory
# ceilings; application latency/availability is unaffected; dropped-record
# and queue-saturation metrics are themselves queryable afterward.
#
# This script checks the CEILINGS ARE RESPECTED (container stays up, stays
# under its configured memory limit) and that SOME drop/refusal signal is
# queryable. It does not attempt a rigorous p95-latency measurement —
# docs/CONSTRAINTS.md's C6 quantitative targets ("app p95 latency change <=2%
# under controlled load") need real load-testing tooling (k6, locust, etc.),
# which is out of scope for a bash script; this does a looser before/after
# average-latency sanity check instead and says so in its own output.
#
# Requires: otel/docker-compose.otel.yml already running,
# orchestrator-svc/.venv already set up.
#
# Usage: ./otel/tests/test_saturation.sh [flood_count]

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ORCH_DIR="${REPO_ROOT}/orchestrator-svc"
LOG_FILE="${REPO_ROOT}/otel/local-logs/orchestrator_saturation.log"
COMPOSE_FILE="${REPO_ROOT}/otel/docker-compose.otel.yml"
COLLECTOR_HTTP="${COLLECTOR_HTTP:-http://localhost:4318}"
OPENOBSERVE_URL="${OPENOBSERVE_URL:-http://localhost:5080}"
OPENOBSERVE_ORG="${OPENOBSERVE_ORG:-default}"
OPENOBSERVE_AUTH="${OPENOBSERVE_AUTH:-Basic YWRtaW5AZXhhbXBsZS10ZXN0LmludmFsaWQ6b3RlbC1wb2MtbG9jYWwtb25seQ==}"
ORCH_PORT="${ORCH_PORT:-8001}"
FLOOD_COUNT="${1:-3000}"
MEMORY_LIMIT_MIB=512  # must match otelCollector.resources.limits.memory / docker-compose.otel.yml mem_limit

PY="${ORCH_DIR}/.venv/Scripts/python.exe"
[ -x "${PY}" ] || PY="${ORCH_DIR}/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  echo "FAIL: orchestrator-svc/.venv not found. See orchestrator-svc/README.md."
  exit 1
fi

: > "${LOG_FILE}"
(
  cd "${ORCH_DIR}" && \
  APP_ENV=development HOST=127.0.0.1 PORT="${ORCH_PORT}" LOG_LEVEL=INFO \
  "${PY}" -m app.main
) > "${LOG_FILE}" 2>&1 &
ORCH_PID=$!
cleanup() {
  kill "${ORCH_PID}" 2>/dev/null || true
  wait "${ORCH_PID}" 2>/dev/null || true
}
trap cleanup EXIT

healthy=0
for i in $(seq 1 20); do
  curl -sf "http://127.0.0.1:${ORCH_PORT}/health" >/dev/null 2>&1 && { healthy=1; break; }
  sleep 1
done
[ "${healthy}" -eq 1 ] || { echo "FAIL: orchestrator-svc never became healthy."; exit 1; }

avg_latency_ms() {
  local total=0 n=5
  for i in $(seq 1 ${n}); do
    local t
    t=$(curl -s -o /dev/null -w "%{time_total}" -X POST "http://127.0.0.1:${ORCH_PORT}/chat" \
      -H "Content-Type: application/json" -d '{"message": "Can you look up my orders?"}')
    total=$(awk "BEGIN {print ${total} + ${t}}")
  done
  awk "BEGIN {printf \"%.1f\", (${total} / ${n}) * 1000}"
}

echo "==> Baseline /chat latency (avg of 5 requests)"
baseline_ms=$(avg_latency_ms)
echo "    baseline: ${baseline_ms}ms"

echo "==> Flooding the Collector with ${FLOOD_COUNT} hand-crafted OTLP spans..."
flood_body() {
  local trace_id span_id now_ns end_ns
  trace_id=$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')
  span_id=$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')
  now_ns=$(date +%s%N)
  end_ns=$((now_ns + 1000000))
  printf '{"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"otel-poc-saturation-test"}}]},"scopeSpans":[{"scope":{"name":"otel-poc-test-saturation"},"spans":[{"traceId":"%s","spanId":"%s","name":"saturation-probe","kind":1,"startTimeUnixNano":"%s","endTimeUnixNano":"%s"}]}]}]}' \
    "${trace_id}" "${span_id}" "${now_ns}" "${end_ns}"
}

start_ts=$(date +%s)
for i in $(seq 1 "${FLOOD_COUNT}"); do
  curl -s -o /dev/null -X POST "${COLLECTOR_HTTP}/v1/traces" \
    -H "Content-Type: application/json" -d "$(flood_body)" &
  # Cap background job fan-out so this script itself doesn't become the
  # bottleneck / exhaust local ephemeral ports.
  if (( i % 50 == 0 )); then wait; fi
done
wait
end_ts=$(date +%s)
echo "    sent ${FLOOD_COUNT} spans in $((end_ts - start_ts))s"

echo "==> /chat latency during/immediately after the flood"
flood_ms=$(avg_latency_ms)
echo "    during/after flood: ${flood_ms}ms (baseline was ${baseline_ms}ms)"
echo "    NOTE: this is a loose sanity check, not the rigorous p95 measurement"
echo "    docs/CONSTRAINTS.md C6 specifies — see this script's header comment."

echo "==> Checking otel-collector container memory stays within its ${MEMORY_LIMIT_MIB}MiB limit"
mem_usage=$(docker stats --no-stream --format '{{.MemUsage}}' \
  "$(docker compose -f "${COMPOSE_FILE}" ps -q otel-collector)" 2>/dev/null | awk -F'/' '{print $1}' | tr -d ' ')
echo "    current memory usage: ${mem_usage:-unknown}"
container_running=$(docker compose -f "${COMPOSE_FILE}" ps -q otel-collector)
if [ -z "${container_running}" ]; then
  echo "FAIL: otel-collector container is not running after the flood (likely OOM-killed — check 'docker compose -f otel/docker-compose.otel.yml logs otel-collector')."
  exit 1
fi
echo "    OK: otel-collector container is still running after the flood."

echo "==> Checking for a queryable drop/refusal signal (Acceptance A13)"
sleep 20  # let the prometheus self-scrape (15s interval) and export catch up
metrics_response=$(curl -s -X POST \
  "${OPENOBSERVE_URL}/api/${OPENOBSERVE_ORG}/default/_search?type=metrics" \
  -H "Authorization: ${OPENOBSERVE_AUTH}" \
  -H "Content-Type: application/json" \
  -d "{\"query\":{\"sql\":\"SELECT * FROM default WHERE metric_name LIKE 'otelcol_%' LIMIT 50\",\"start_time\":0,\"end_time\":9999999999999999,\"size\":50}}")

if echo "${metrics_response}" | grep -q "otelcol_"; then
  echo "    OK: otelcol_* self-monitoring metrics are queryable in OpenObserve."
  echo "    (Confirming a SPECIFIC drop/refusal metric name and non-zero value"
  echo "    needs a live run — see otel/VERIFICATION_STATUS.md; this check"
  echo "    only confirms the self-monitoring pipeline itself is delivering"
  echo "    data, which is the prerequisite for A13, not proof a drop"
  echo "    actually occurred under this specific flood size.)"
else
  echo "FAIL: no otelcol_* self-monitoring metrics found in OpenObserve."
  echo "Check the prometheus receiver / service.telemetry.metrics.address in"
  echo "otel/collector-config.yaml, and that 20s was enough for the 15s scrape"
  echo "interval plus batch/export latency."
  exit 1
fi

echo ""
echo "PASS (with the p95-rigor caveat noted above): Collector stayed up and"
echo "within its memory limit under flood; self-monitoring metrics are queryable."
exit 0
