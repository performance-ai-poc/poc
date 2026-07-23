#!/usr/bin/env bash
# Acceptance A2 (docs/ACCEPTANCE.md): run one /chat request with the
# application completely unmodified; every api.request.* and agent.* event
# from that request appears in OpenObserve, correlated by run_id.
#
# This is the single highest-value verification in the whole plan (see
# docs/OTEL_PLAN.md Phase 2): it proves the pluginless claim. No file under
# orchestrator-svc/ or mcp-server/ is touched by this script — it starts the
# unmodified service the same way orchestrator-svc/README.md's own "Running
# Locally" section documents, and only redirects that already-existing
# stdout stream into a file for the Collector to read.
#
# Requires:
#   - otel/docker-compose.otel.yml already running
#     (docker compose -f otel/docker-compose.otel.yml up -d)
#   - orchestrator-svc/.venv already created with requirements.txt installed
#     (see orchestrator-svc/README.md "Running Locally")
#
# Usage: ./otel/tests/test_filelog_ingest.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ORCH_DIR="${REPO_ROOT}/orchestrator-svc"
LOG_FILE="${REPO_ROOT}/otel/local-logs/orchestrator.log"
OPENOBSERVE_URL="${OPENOBSERVE_URL:-http://localhost:5080}"
OPENOBSERVE_ORG="${OPENOBSERVE_ORG:-default}"
OPENOBSERVE_AUTH="${OPENOBSERVE_AUTH:-Basic YWRtaW5AZXhhbXBsZS10ZXN0LmludmFsaWQ6b3RlbC1wb2MtbG9jYWwtb25seQ==}"
ORCH_PORT="${ORCH_PORT:-8001}"

if [ ! -x "${ORCH_DIR}/.venv/Scripts/python.exe" ] && [ ! -x "${ORCH_DIR}/.venv/bin/python" ]; then
  echo "FAIL: orchestrator-svc/.venv not found. Set it up per orchestrator-svc/README.md first:"
  echo "  cd orchestrator-svc && python -m venv .venv && ./.venv/*/pip install -r requirements.txt"
  exit 1
fi
PY="${ORCH_DIR}/.venv/Scripts/python.exe"
[ -x "${PY}" ] || PY="${ORCH_DIR}/.venv/bin/python"

: > "${LOG_FILE}"
echo "==> Starting orchestrator-svc unmodified, stdout redirected to ${LOG_FILE}"

# The orchestrator itself is started exactly as its own README documents
# (`python -m app.main`) — nothing here passes it a flag, an env var, or a
# code path it doesn't already have. AGENT_LIVE_CALLS is left at its default
# (false / offline) deliberately: offline mode is documented
# (orchestrator-svc/README.md) to emit the identical agent.* telemetry as
# live mode, without needing a reachable LLM/MCP server for this verification.
(
  cd "${ORCH_DIR}" && \
  APP_ENV=development HOST=127.0.0.1 PORT="${ORCH_PORT}" LOG_LEVEL=INFO \
  "${PY}" -m app.main
) > "${LOG_FILE}" 2>&1 &
ORCH_PID=$!

cleanup() {
  echo "==> Stopping orchestrator-svc (pid ${ORCH_PID})"
  kill "${ORCH_PID}" 2>/dev/null || true
  wait "${ORCH_PID}" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Waiting for orchestrator-svc to become healthy on :${ORCH_PORT}..."
healthy=0
for i in $(seq 1 20); do
  if curl -sf "http://127.0.0.1:${ORCH_PORT}/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done
if [ "${healthy}" -ne 1 ]; then
  echo "FAIL: orchestrator-svc never became healthy. Log so far:"
  cat "${LOG_FILE}"
  exit 1
fi

MARKER_SESSION="otel-poc-filelog-test-$$"
echo "==> Sending POST /chat (session_id=${MARKER_SESSION}, routes to api_agent so agent.llm_call + tool events fire)"

CHAT_RESPONSE=$(curl -s -X POST "http://127.0.0.1:${ORCH_PORT}/chat" \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"Check the shipment status with the carrier please.\", \"session_id\": \"${MARKER_SESSION}\"}")

RUN_ID=$(echo "${CHAT_RESPONSE}" | grep -oE '"run_id"\s*:\s*"[^"]+"' | head -1 | sed -E 's/.*:\s*"([^"]+)"/\1/')

if [ -z "${RUN_ID}" ]; then
  echo "FAIL: /chat did not return a run_id. Response:"
  echo "${CHAT_RESPONSE}"
  exit 1
fi
echo "==> /chat responded, run_id=${RUN_ID}"

# Give the filelog receiver its poll interval plus the batch processor's
# timeout (collector-config.yaml: 3s) before checking — this is the same
# reason test_collector_up.sh polls rather than sleeping once.
echo "==> Waiting up to 40s for the Collector to pick up the log file and export it..."
found_events=""
for i in $(seq 1 20); do
  sleep 2
  search_response=$(curl -s -X POST \
    "${OPENOBSERVE_URL}/api/${OPENOBSERVE_ORG}/_search?type=logs" \
    -H "Authorization: ${OPENOBSERVE_AUTH}" \
    -H "Content-Type: application/json" \
    -d "{\"query\":{\"sql\":\"SELECT event FROM default WHERE run_id='${RUN_ID}'\",\"start_time\":$(( $(date +%s)000000 - 3600000000 )),\"end_time\":$(( $(date +%s)000000 + 3600000000 )),\"size\":50}}")

  if echo "${search_response}" | grep -q "${RUN_ID}"; then
    found_events="${search_response}"
    break
  fi
  echo "    (attempt ${i}/20: not yet visible)"
done

if [ -z "${found_events}" ]; then
  echo "FAIL: no record with run_id=${RUN_ID} ever appeared in OpenObserve."
  echo "Check: is the filelog receiver's include path (/var/log/app/*.log in"
  echo "collector-config.yaml) actually bind-mounted to ${LOG_FILE}'s directory"
  echo "in otel/docker-compose.otel.yml? Is the Collector container running?"
  exit 1
fi

fail=0
for expected_event in "api.request.started" "api.request.completed" "agent.step_started" "agent.step_completed"; do
  if ! echo "${found_events}" | grep -q "${expected_event}"; then
    echo "FAIL: expected event '${expected_event}' not found for run_id=${RUN_ID}"
    fail=1
  fi
done

if [ "${fail}" -eq 1 ]; then
  echo ""
  echo "Partial or no match. Full search response:"
  echo "${found_events}"
  exit 1
fi

echo "PASS: api.request.* and agent.* events for run_id=${RUN_ID} are queryable in OpenObserve."
echo "The application (orchestrator-svc) was started exactly as documented, with no code change."
exit 0
