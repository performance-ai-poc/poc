#!/usr/bin/env bash
# Acceptance A1 (docs/ACCEPTANCE.md): a hand-crafted OTLP span sent via curl
# appears in OpenObserve. This is Phase 1's only verification — it proves the
# Collector receives, processes, and exports before anything touches the
# application's real telemetry (Phase 2).
#
# Usage:
#   docker compose -f otel/docker-compose.otel.yml up -d
#   ./otel/tests/test_collector_up.sh
#
# Exits 0 on pass, 1 on failure, with the reason printed either way.

set -uo pipefail

COLLECTOR_HTTP="${COLLECTOR_HTTP:-http://localhost:4318}"
OPENOBSERVE_URL="${OPENOBSERVE_URL:-http://localhost:5080}"
OPENOBSERVE_ORG="${OPENOBSERVE_ORG:-default}"
OPENOBSERVE_AUTH="${OPENOBSERVE_AUTH:-Basic YWRtaW5AZXhhbXBsZS10ZXN0LmludmFsaWQ6b3RlbC1wb2MtbG9jYWwtb25seQ==}"

# 32 hex chars / 16 hex chars — valid OTLP trace_id / span_id, unique per run
# so this test never matches a stale record from a previous run.
TRACE_ID=$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')
SPAN_ID=$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')
SPAN_NAME="otel-poc-test-collector-up-$$"
NOW_NS=$(date +%s%N)
END_NS=$((NOW_NS + 1000000))

echo "==> test_collector_up: sending hand-crafted OTLP span (trace_id=${TRACE_ID}, name=${SPAN_NAME})"

BODY=$(cat <<EOF
{
  "resourceSpans": [{
    "resource": {
      "attributes": [{"key": "service.name", "value": {"stringValue": "otel-poc-verification"}}]
    },
    "scopeSpans": [{
      "scope": {"name": "otel-poc-test-collector-up"},
      "spans": [{
        "traceId": "${TRACE_ID}",
        "spanId": "${SPAN_ID}",
        "name": "${SPAN_NAME}",
        "kind": 1,
        "startTimeUnixNano": "${NOW_NS}",
        "endTimeUnixNano": "${END_NS}",
        "attributes": [{"key": "test.marker", "value": {"stringValue": "phase1-verification"}}]
      }]
    }]
  }]
}
EOF
)

http_status=$(curl -s -o /tmp/otel_poc_send_response.json -w "%{http_code}" \
  -X POST "${COLLECTOR_HTTP}/v1/traces" \
  -H "Content-Type: application/json" \
  -d "${BODY}")

if [ "${http_status}" != "200" ]; then
  echo "FAIL: Collector rejected the span (HTTP ${http_status})"
  cat /tmp/otel_poc_send_response.json 2>/dev/null
  exit 1
fi

echo "==> Collector accepted the span. Polling OpenObserve for up to 30s..."

# The batch processor's timeout (3s, collector-config.yaml) plus export +
# ingest latency means the span will not be queryable instantly. Poll rather
# than sleep-once, so this script is not flaky under slow CI/local machines.
found=0
for i in $(seq 1 15); do
  sleep 2
  search_response=$(curl -s -X POST \
    "${OPENOBSERVE_URL}/api/${OPENOBSERVE_ORG}/_search?type=traces" \
    -H "Authorization: ${OPENOBSERVE_AUTH}" \
    -H "Content-Type: application/json" \
    -d "{\"query\":{\"sql\":\"SELECT trace_id, span_id, service_name FROM default WHERE trace_id='${TRACE_ID}'\",\"start_time\":$(( $(date +%s)000000 - 3600000000 )),\"end_time\":$(( $(date +%s)000000 + 3600000000 )),\"size\":10}}")

  if echo "${search_response}" | grep -q "${TRACE_ID}"; then
    found=1
    break
  fi
  echo "    (attempt ${i}/15: not yet visible)"
done

if [ "${found}" -eq 1 ]; then
  echo "PASS: span ${SPAN_NAME} (trace_id=${TRACE_ID}) found in OpenObserve."
  exit 0
fi

echo "FAIL: span never appeared in OpenObserve after 30s."
echo "Last search response:"
echo "${search_response}"
echo ""
echo "If OpenObserve's OTLP trace stream/query shape differs from what this"
echo "script assumes (stream name 'default', SQL-style _search with a"
echo "trace_id column), check the OpenObserve version pinned in"
echo "otel/docker-compose.otel.yml against its current docs (CONSTRAINTS.md:"
echo "\"check upstream docs rather than guessing\") and adjust the query above"
echo "— this script's assumptions, not the Collector config, are the most"
echo "likely thing to need updating."
exit 1
