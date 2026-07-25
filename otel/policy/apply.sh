#!/usr/bin/env bash
# Apply a capture policy: read captureMode out of the named policy file,
# render it as the CAPTURE_MODE environment variable the Collector reads
# (collector-config.yaml's transform/limits processor, via ${env:CAPTURE_MODE}),
# and recreate ONLY the otel-collector container so the new value takes
# effect. See otel/policy/README.md for why this restarts the Collector
# container specifically and never the application.
#
# Usage:
#   ./otel/policy/apply.sh metadata-only
#   ./otel/policy/apply.sh content-approved

set -euo pipefail

MODE="${1:-}"
if [ "${MODE}" != "metadata-only" ] && [ "${MODE}" != "content-approved" ]; then
  echo "Usage: $0 <metadata-only|content-approved>"
  echo "(must match a captureMode value in otel/policy/policy.schema.json)"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
POLICY_DIR="${REPO_ROOT}/otel/policy"
POLICY_FILE="${POLICY_DIR}/${MODE}.yaml"
ENV_FILE="${POLICY_DIR}/.env.active"
COMPOSE_FILE="${REPO_ROOT}/otel/docker-compose.otel.yml"

if [ ! -f "${POLICY_FILE}" ]; then
  echo "FAIL: ${POLICY_FILE} does not exist."
  exit 1
fi

# Confirm the file's own captureMode agrees with the requested mode (catches
# a copy/paste or rename mistake before it gets rendered into the Collector's
# environment).
file_mode=$(grep -E '^captureMode:' "${POLICY_FILE}" | sed -E 's/^captureMode:\s*//' | tr -d '\r')
if [ "${file_mode}" != "${MODE}" ]; then
  echo "FAIL: ${POLICY_FILE} declares captureMode: ${file_mode}, expected ${MODE}."
  exit 1
fi

echo "CAPTURE_MODE=${MODE}" > "${ENV_FILE}"
echo "==> Wrote ${ENV_FILE} (CAPTURE_MODE=${MODE})"

echo "==> Recreating otel-collector only. orchestrator-svc and mcp-server are"
echo "    not part of this compose file and are not touched (C1/C2) — this is"
echo "    a Collector-only restart, never an application restart."
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" up -d --force-recreate otel-collector

echo "==> Applied ${MODE}. Verify with:"
echo "    docker compose -f ${COMPOSE_FILE} exec otel-collector env | grep CAPTURE_MODE"
