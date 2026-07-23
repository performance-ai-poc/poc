#!/usr/bin/env bash
# Acceptance A8 (docs/ACCEPTANCE.md): seed exact sensitive values into a
# hand-crafted span and assert none reach the backend. Defense in depth — the
# application-side emitters already guarantee E2-E6 (docs/ACCEPTANCE.md);
# this proves the Collector's own privacy processor independently, so a
# future application bug is not the only thing standing between a secret and
# the backend.
#
# The fourth seeded value (an attribute name mentioned nowhere in
# collector-config.yaml) is what actually distinguishes an allowlist (C4)
# from a denylist: nothing had to know its name in advance for it to be
# dropped.
#
# Usage:
#   docker compose -f otel/docker-compose.otel.yml up -d
#   ./otel/tests/test_redaction.sh

set -uo pipefail

COLLECTOR_HTTP="${COLLECTOR_HTTP:-http://localhost:4318}"
OPENOBSERVE_URL="${OPENOBSERVE_URL:-http://localhost:5080}"
OPENOBSERVE_ORG="${OPENOBSERVE_ORG:-default}"
OPENOBSERVE_AUTH="${OPENOBSERVE_AUTH:-Basic YWRtaW5AZXhhbXBsZS10ZXN0LmludmFsaWQ6b3RlbC1wb2MtbG9jYWwtb25seQ==}"

TRACE_ID=$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')
SPAN_ID=$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')
SPAN_NAME="otel-poc-test-redaction-$$"
NOW_NS=$(date +%s%N)
END_NS=$((NOW_NS + 1000000))

API_KEY_VALUE="SEEDED_APIKEY_d41d8cd98f00b204"
EMAIL_VALUE="seeded.user@example-test.invalid"
AUTH_HEADER_VALUE="Bearer SEEDED_TOKEN_9e107d9d372bb682"
UNKNOWN_ATTR_NAME="x-seeded-unexpected-header"
UNKNOWN_ATTR_VALUE="SEEDED_UNKNOWN_VALUE_should_never_arrive"

echo "==> test_redaction: sending span with 4 seeded sensitive/unknown values (trace_id=${TRACE_ID})"

BODY=$(cat <<EOF
{
  "resourceSpans": [{
    "resource": {
      "attributes": [{"key": "service.name", "value": {"stringValue": "otel-poc-verification"}}]
    },
    "scopeSpans": [{
      "scope": {"name": "otel-poc-test-redaction"},
      "spans": [{
        "traceId": "${TRACE_ID}",
        "spanId": "${SPAN_ID}",
        "name": "${SPAN_NAME}",
        "kind": 1,
        "startTimeUnixNano": "${NOW_NS}",
        "endTimeUnixNano": "${END_NS}",
        "attributes": [
          {"key": "api_key", "value": {"stringValue": "${API_KEY_VALUE}"}},
          {"key": "user.email", "value": {"stringValue": "${EMAIL_VALUE}"}},
          {"key": "http.request.header.authorization", "value": {"stringValue": "${AUTH_HEADER_VALUE}"}},
          {"key": "${UNKNOWN_ATTR_NAME}", "value": {"stringValue": "${UNKNOWN_ATTR_VALUE}"}},
          {"key": "run_id", "value": {"stringValue": "redaction-test-run-id"}}
        ]
      }]
    }]
  }]
}
EOF
)

http_status=$(curl -s -o /tmp/otel_poc_redaction_send.json -w "%{http_code}" \
  -X POST "${COLLECTOR_HTTP}/v1/traces" \
  -H "Content-Type: application/json" \
  -d "${BODY}")

if [ "${http_status}" != "200" ]; then
  echo "FAIL: Collector rejected the span (HTTP ${http_status})"
  cat /tmp/otel_poc_redaction_send.json 2>/dev/null
  exit 1
fi

echo "==> Collector accepted the span. Polling OpenObserve for up to 30s for the record to land (by run_id, not the seeded values, since those must never appear)..."

found=0
for i in $(seq 1 15); do
  sleep 2
  search_response=$(curl -s -X POST \
    "${OPENOBSERVE_URL}/api/${OPENOBSERVE_ORG}/default/_search?type=traces" \
    -H "Authorization: ${OPENOBSERVE_AUTH}" \
    -H "Content-Type: application/json" \
    -d "{\"query\":{\"sql\":\"SELECT * FROM default WHERE trace_id='${TRACE_ID}'\",\"start_time\":0,\"end_time\":9999999999999999,\"size\":10}}")

  if echo "${search_response}" | grep -q "${TRACE_ID}"; then
    found=1
    break
  fi
  echo "    (attempt ${i}/15: not yet visible)"
done

if [ "${found}" -eq 0 ]; then
  echo "FAIL: record never appeared at all — cannot verify redaction against a record that isn't there."
  echo "Run test_collector_up.sh first to confirm basic ingestion works."
  exit 1
fi

fail=0
for needle in "${API_KEY_VALUE}" "${EMAIL_VALUE}" "${AUTH_HEADER_VALUE}" "${UNKNOWN_ATTR_VALUE}" "${UNKNOWN_ATTR_NAME}"; do
  if echo "${search_response}" | grep -qF -- "${needle}"; then
    echo "FAIL: seeded value/key leaked into the backend: ${needle}"
    fail=1
  fi
done

if [ "${fail}" -eq 1 ]; then
  echo ""
  echo "One or more seeded values reached OpenObserve. Check:"
  echo "  - attributes/privacy processor actions in otel/collector-config.yaml"
  echo "  - transform/limits' keep_keys() allowlist (the unknown-attr case"
  echo "    specifically tests this, not the named-delete list above it)"
  exit 1
fi

echo "PASS: none of the 4 seeded sensitive/unknown values reached OpenObserve."
exit 0
