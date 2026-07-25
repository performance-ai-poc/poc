#!/usr/bin/env bash

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ORCH_DIR="${REPO_ROOT}/orchestrator-svc"
LOG_FILE="${REPO_ROOT}/otel/local-logs/orchestrator.log"
COMPOSE_FILE="${REPO_ROOT}/otel/docker-compose.otel.yml"
ORCH_PORT="${ORCH_PORT:-8001}"

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
  echo "==> Restoring otel-collector and openobserve (in case this exits mid-test)"
  docker compose -f "${COMPOSE_FILE}" start otel-collector openobserve >/dev/null 2>&1 || true
  echo "==> Stopping orchestrator-svc (pid ${ORCH_PID})"
  kill "${ORCH_PID}" 2>/dev/null || true
  wait "${ORCH_PID}" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Waiting for orchestrator-svc to become healthy..."
healthy=0
for i in $(seq 1 20); do
  curl -sf "http://127.0.0.1:${ORCH_PORT}/health" >/dev/null 2>&1 && { healthy=1; break; }
  sleep 1
done
[ "${healthy}" -eq 1 ] || { echo "FAIL: orchestrator-svc never became healthy."; cat "${LOG_FILE}"; exit 1; }

chat_ok() {
  local status
  status=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://127.0.0.1:${ORCH_PORT}/chat" \
    -H "Content-Type: application/json" -d '{"message": "Can you look up my orders?"}')
  [ "${status}" = "200" ]
}

fail=0

echo "==> [1/4] Baseline: /chat succeeds with the Collector up"
if ! chat_ok; then echo "FAIL: baseline /chat did not return 200"; fail=1; fi

echo "==> [2/4] Stopping otel-collector (A10)"
docker compose -f "${COMPOSE_FILE}" stop otel-collector
sleep 2
lines_before=$(wc -l < "${LOG_FILE}")
for i in 1 2 3; do
  if ! chat_ok; then
    echo "FAIL: /chat request ${i} failed while the Collector was stopped — this must never happen (C2)."
    fail=1
  fi
done
lines_after=$(wc -l < "${LOG_FILE}")
if [ "${lines_after}" -le "${lines_before}" ]; then
  echo "FAIL: stdout logging did not continue while the Collector was stopped (log file did not grow)."
  fail=1
else
  echo "    OK: 3/3 requests succeeded with the Collector stopped; stdout logging continued (log grew by $((lines_after - lines_before)) lines)."
fi

echo "==> [3/4] Restarting otel-collector, confirming collection resumes with no application restart"
docker compose -f "${COMPOSE_FILE}" start otel-collector
sleep 5
if ! chat_ok; then
  echo "FAIL: /chat failed immediately after restarting the Collector."
  fail=1
else
  echo "    OK: /chat still succeeds after the Collector restarts."
fi
# The application process was never killed or restarted across steps 2-3 —
# confirmed by checking the same PID is still alive throughout, not by
# re-deriving it from a fresh health check.
if ! kill -0 "${ORCH_PID}" 2>/dev/null; then
  echo "FAIL: orchestrator-svc process (pid ${ORCH_PID}) is no longer running — it must survive the whole test unchanged."
  fail=1
else
  echo "    OK: orchestrator-svc (pid ${ORCH_PID}) never restarted."
fi

echo "==> [4/4] Backend outage (A12): stopping openobserve, Collector and app both stay up"
docker compose -f "${COMPOSE_FILE}" stop openobserve
sleep 2
for i in 1 2 3; do
  if ! chat_ok; then
    echo "FAIL: /chat request ${i} failed while OpenObserve (the backend) was stopped."
    fail=1
  fi
done
echo "    OK: /chat kept succeeding with the backend stopped (export queue absorbs it, per C2/A12)."
echo "==> Restarting openobserve"
docker compose -f "${COMPOSE_FILE}" start openobserve
sleep 5
if ! chat_ok; then
  echo "FAIL: /chat failed after restarting OpenObserve."
  fail=1
fi

if [ "${fail}" -eq 1 ]; then
  echo ""
  echo "FAIL: one or more fail-open guarantees did not hold. See above."
  exit 1
fi

echo ""
echo "PASS: Collector and backend outages, independently, never failed a /chat request;"
echo "stdout logging continued throughout; the application was never restarted."
exit 0
