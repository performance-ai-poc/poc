#!/usr/bin/env bash

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COLLECTOR_HTTP="${COLLECTOR_HTTP:-http://localhost:4318}"
OPENOBSERVE_URL="${OPENOBSERVE_URL:-http://localhost:5080}"
OPENOBSERVE_ORG="${OPENOBSERVE_ORG:-default}"
OPENOBSERVE_AUTH="${OPENOBSERVE_AUTH:-Basic YWRtaW5AZXhhbXBsZS10ZXN0LmludmFsaWQ6b3RlbC1wb2MtbG9jYWwtb25seQ==}"

cleanup() {
  echo "==> Restoring metadata-only (the default policy) before exit"
  "${REPO_ROOT}/otel/policy/apply.sh" metadata-only >/dev/null 2>&1 || true
}
trap cleanup EXIT

send_probe_span() {
  local trace_id="$1" span_id="$2" span_name="$3"
  local now_ns end_ns
  now_ns=$(date +%s%N)
  end_ns=$((now_ns + 1000000))
  curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${COLLECTOR_HTTP}/v1/traces" \
    -H "Content-Type: application/json" \
    -d "{
      \"resourceSpans\": [{
        \"resource\": {\"attributes\": [{\"key\": \"service.name\", \"value\": {\"stringValue\": \"otel-poc-verification\"}}]},
        \"scopeSpans\": [{
          \"scope\": {\"name\": \"otel-poc-test-policy-switch\"},
          \"spans\": [{
            \"traceId\": \"${trace_id}\",
            \"spanId\": \"${span_id}\",
            \"name\": \"${span_name}\",
            \"kind\": 1,
            \"startTimeUnixNano\": \"${now_ns}\",
            \"endTimeUnixNano\": \"${end_ns}\",
            \"attributes\": [
              {\"key\": \"gen_ai.input.messages\", \"value\": {\"stringValue\": \"POLICY_PROBE_INPUT_MESSAGE\"}},
              {\"key\": \"gen_ai.output.messages\", \"value\": {\"stringValue\": \"POLICY_PROBE_OUTPUT_MESSAGE\"}}
            ]
          }]
        }]
      }]
    }"
}

search_for_trace() {
  local trace_id="$1"
  local response=""
  for i in $(seq 1 15); do
    sleep 2
    response=$(curl -s -X POST \
      "${OPENOBSERVE_URL}/api/${OPENOBSERVE_ORG}/_search?type=traces" \
      -H "Authorization: ${OPENOBSERVE_AUTH}" \
      -H "Content-Type: application/json" \
      -d "{\"query\":{\"sql\":\"SELECT * FROM default WHERE trace_id='${trace_id}'\",\"start_time\":$(( $(date +%s)000000 - 3600000000 )),\"end_time\":$(( $(date +%s)000000 + 3600000000 )),\"size\":10}}")
    if echo "${response}" | grep -q "${trace_id}"; then
      echo "${response}"
      return 0
    fi
  done
  echo ""
  return 1
}

# --- 1. metadata-only: content attributes must NOT reach the backend -------
echo "==> Applying metadata-only"
"${REPO_ROOT}/otel/policy/apply.sh" metadata-only

TRACE_1=$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')
SPAN_1=$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')
echo "==> Sending probe span under metadata-only (trace_id=${TRACE_1})"
send_probe_span "${TRACE_1}" "${SPAN_1}" "otel-poc-policy-probe-metadata-only-$$" >/dev/null

result_1=$(search_for_trace "${TRACE_1}")
if [ -z "${result_1}" ]; then
  echo "FAIL: probe span never appeared at all under metadata-only — cannot evaluate the policy."
  exit 1
fi
if echo "${result_1}" | grep -q "POLICY_PROBE_INPUT_MESSAGE\|POLICY_PROBE_OUTPUT_MESSAGE"; then
  echo "FAIL: content attributes leaked through under metadata-only (should have been deleted)."
  exit 1
fi
echo "    OK: content attributes absent under metadata-only, as expected."

# --- 2. content-approved: content attributes MUST reach the backend --------
echo "==> Applying content-approved"
"${REPO_ROOT}/otel/policy/apply.sh" content-approved

TRACE_2=$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')
SPAN_2=$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')
echo "==> Sending probe span under content-approved (trace_id=${TRACE_2})"
send_probe_span "${TRACE_2}" "${SPAN_2}" "otel-poc-policy-probe-content-approved-$$" >/dev/null

result_2=$(search_for_trace "${TRACE_2}")
if [ -z "${result_2}" ]; then
  echo "FAIL: probe span never appeared at all under content-approved."
  exit 1
fi
if ! echo "${result_2}" | grep -q "POLICY_PROBE_INPUT_MESSAGE"; then
  echo "FAIL: gen_ai.input.messages did not reach the backend under content-approved."
  exit 1
fi
if ! echo "${result_2}" | grep -q "POLICY_PROBE_OUTPUT_MESSAGE"; then
  echo "FAIL: gen_ai.output.messages did not reach the backend under content-approved."
  exit 1
fi
echo "    OK: content attributes present under content-approved, as expected."

echo ""
echo "PASS: capture.mode visibly changes what reaches the backend."
echo "(cleanup will now restore metadata-only)"
exit 0
