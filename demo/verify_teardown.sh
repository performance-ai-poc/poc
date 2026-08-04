#!/usr/bin/env bash
#
# Post-uninstall verification for the observability teardown demo.
#
# Run this AFTER removing the Observability Plane (obs-ctl option 2, or
# `make uninstall-observability`). It proves the point the demo is making:
# the observability stack is gone, and the multi-agent application never
# noticed.
#
# Read-only — it inspects the cluster and sends one chat request. It never
# installs, uninstalls or deletes anything, so it is safe to re-run.

set -uo pipefail

# Release/namespace defaults mirror make/common.mk so this agrees with the rest
# of the repo; override via the environment to match a custom deploy.
NAMESPACE="${NAMESPACE:-default}"
RELEASE="${RELEASE:-demo}"
OBSERVABILITY_RELEASE="${OBSERVABILITY_RELEASE:-observability}"
# Deliberately not 8001 (make/port-forward.mk's dev port) — see the port-forward
# note in check 4 for why this check insists on its own forward.
ORCHESTRATOR_LOCAL_PORT="${ORCHESTRATOR_LOCAL_PORT:-18001}"
ORCHESTRATOR_RESOURCE="${RELEASE}-ai-chat-orchestrator"

# Both charts stamp app.kubernetes.io/instance with the release name on every
# pod template, so the release name alone selects everything a chart owns.
OBS_SELECTOR="app.kubernetes.io/instance=${OBSERVABILITY_RELEASE}"
APP_SELECTOR="app.kubernetes.io/instance=${RELEASE}"

failures=0
PORT_FORWARD_PID=""

pass() { echo "    OK: $*"; }
fail() { echo "  FAIL: $*"; failures=$((failures + 1)); }
step() { echo; echo "==> $*"; }

cleanup() {
  if [ -n "${PORT_FORWARD_PID}" ]; then
    kill "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
    wait "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# -----------------------------------------------------------------------------
# Preconditions
# -----------------------------------------------------------------------------

step "Checking prerequisites"

for binary in kubectl helm curl; do
  if ! command -v "${binary}" >/dev/null 2>&1; then
    echo "  FAIL: ${binary} is not installed or not on PATH."
    exit 1
  fi
done
pass "kubectl, helm and curl are on PATH"

if ! kubectl cluster-info >/dev/null 2>&1; then
  echo "  FAIL: no reachable Kubernetes cluster (kubectl cluster-info failed)."
  echo "        Start one with 'make minikube-start' and deploy with 'make dev'."
  exit 1
fi
pass "cluster is reachable"

echo
echo "    namespace:             ${NAMESPACE}"
echo "    app release:           ${RELEASE}"
echo "    observability release: ${OBSERVABILITY_RELEASE}"

# -----------------------------------------------------------------------------
# 1. The observability Helm release is gone
# -----------------------------------------------------------------------------

step "1/4  Observability Helm release removed"

helm_releases="$(helm list -n "${NAMESPACE}" -q 2>/dev/null)"

if echo "${helm_releases}" | grep -qx "${OBSERVABILITY_RELEASE}"; then
  fail "Helm release '${OBSERVABILITY_RELEASE}' is still installed in ${NAMESPACE}."
  echo "        Remove it first (obs-ctl option 2, or 'make uninstall-observability')."
else
  pass "Helm release '${OBSERVABILITY_RELEASE}' is not installed"
fi

# -----------------------------------------------------------------------------
# 2. Its workloads are actually gone, not just the release record
# -----------------------------------------------------------------------------

step "2/4  Observability workloads purged from the cluster"

obs_pods="$(kubectl get pods -n "${NAMESPACE}" -l "${OBS_SELECTOR}" \
  --no-headers 2>/dev/null | grep -v '^No resources' | grep -c . )"

if [ "${obs_pods}" -gt 0 ]; then
  fail "${obs_pods} pod(s) matching '${OBS_SELECTOR}' still exist:"
  kubectl get pods -n "${NAMESPACE}" -l "${OBS_SELECTOR}" -o wide 2>/dev/null | sed 's/^/          /'
else
  pass "no pods remain for '${OBS_SELECTOR}'"
fi

# The Collector's ClusterRole/ClusterRoleBinding are cluster-scoped, so a
# botched uninstall can leave them behind after the namespaced objects go.
orphaned_rbac="$(kubectl get clusterrole,clusterrolebinding \
  -l "${OBS_SELECTOR}" --no-headers 2>/dev/null | grep -c . )"

if [ "${orphaned_rbac}" -gt 0 ]; then
  fail "${orphaned_rbac} cluster-scoped RBAC object(s) were left behind:"
  # -o name: two resource types would otherwise print two separate tables,
  # each with its own header.
  kubectl get clusterrole,clusterrolebinding -l "${OBS_SELECTOR}" \
    -o name 2>/dev/null | sed 's/^/          /'
else
  pass "no orphaned ClusterRole/ClusterRoleBinding"
fi

# Helm honours helm.sh/resource-policy: keep, so openObserve.persistence
# .keepOnDelete leaves the OpenObserve PVC behind deliberately — captured
# telemetry survives a reinstall. Surface it rather than staying silent: it
# still carries the observability labels, so a `kubectl get pvc` mid-demo would
# otherwise appear to contradict "purged". A leftover PVC *without* that
# annotation is genuinely orphaned, and does fail.
pvc_states="$(kubectl get pvc -n "${NAMESPACE}" -l "${OBS_SELECTOR}" \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.annotations.helm\.sh/resource-policy}{"\n"}{end}' 2>/dev/null)"

retained="$(echo "${pvc_states}" | awk 'NF && $2 == "keep" { print $1 }')"
orphaned_pvcs="$(echo "${pvc_states}" | awk 'NF && $2 != "keep" { print $1 }')"

if [ -n "${orphaned_pvcs}" ]; then
  fail "PVC(s) left behind with no resource-policy: keep — these are orphans:"
  echo "${orphaned_pvcs}" | sed 's/^/          /'
fi

if [ -n "${retained}" ]; then
  echo "    NOTE: retained by design (helm.sh/resource-policy: keep), not an orphan:"
  echo "${retained}" | sed 's/^/          /'
  echo "          Telemetry is preserved for a reinstall. Delete explicitly to reclaim."
fi

# -----------------------------------------------------------------------------
# 3. The application is untouched
# -----------------------------------------------------------------------------

step "3/4  Multi-agent application still deployed and healthy"

if echo "${helm_releases}" | grep -qx "${RELEASE}"; then
  pass "Helm release '${RELEASE}' is still installed"
else
  fail "Helm release '${RELEASE}' is NOT installed — the app was never deployed,"
  echo "        or the uninstall removed more than it should have."
fi

app_pods="$(kubectl get pods -n "${NAMESPACE}" -l "${APP_SELECTOR}" \
  --no-headers 2>/dev/null | grep -v '^No resources' | grep -c . )"

if [ "${app_pods}" -eq 0 ]; then
  fail "no application pods found for '${APP_SELECTOR}'."
else
  echo
  kubectl get pods -n "${NAMESPACE}" -l "${APP_SELECTOR}" -o wide 2>/dev/null | sed 's/^/    /'
  echo

  # A pod can sit in Running with a container crash-looping behind it, so
  # check the Ready condition rather than trusting the phase alone.
  #
  # Succeeded pods are excluded twice over — once in the jsonpath filter and
  # again in awk. The mcp-seed Job is a post-install Helm hook with
  # hook-delete-policy: before-hook-creation, so its Completed pod legitimately
  # sticks around after install and must not read as a failure.
  pod_states="$(kubectl get pods -n "${NAMESPACE}" -l "${APP_SELECTOR}" \
    -o jsonpath='{range .items[?(@.status.phase!="Succeeded")]}{.metadata.name}{" "}{.status.phase}{" "}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"\n"}{end}' 2>/dev/null)"

  # Long-running pods, i.e. the ones a readiness claim can meaningfully cover.
  serving_pods="$(echo "${pod_states}" | awk 'NF && $2 != "Succeeded"' | grep -c . )"
  completed_pods=$((app_pods - serving_pods))

  not_ready="$(echo "${pod_states}" \
    | awk 'NF && $2 != "Succeeded" && ($2 != "Running" || $3 != "True") { print $1 " (" $2 ", Ready=" $3 ")" }')"

  if [ -n "${not_ready}" ]; then
    fail "these application pods are not Running+Ready:"
    echo "${not_ready}" | sed 's/^/          /'
  elif [ "${serving_pods}" -eq 0 ]; then
    fail "no long-running application pods — only completed ones."
  else
    # Counts the pods actually asserted on, not app_pods, which includes the
    # completed ones this check deliberately skips.
    skipped_note=""
    if [ "${completed_pods}" -gt 0 ]; then
      skipped_note=" (${completed_pods} completed pod(s) skipped)"
    fi
    pass "all ${serving_pods} running application pod(s) are Ready${skipped_note}"
  fi
fi

# -----------------------------------------------------------------------------
# 4. The agents still serve traffic with observability gone
# -----------------------------------------------------------------------------

step "4/4  Agent API resilience (POST /chat)"

ORCH_URL="http://127.0.0.1:${ORCHESTRATOR_LOCAL_PORT}"

# Always establish our own port-forward rather than reusing whatever happens to
# be listening. This check exists to prove the *cluster's* orchestrator survived
# the uninstall, and demo/README.md documents running an orchestrator locally on
# :8001 — reusing that would turn a dead deployment into a false PASS. Hence
# also the uncommon default port: it stays clear of a dev orchestrator or a
# 'make port-forward-orchestrator' already on :8001.
echo "    forwarding service/${ORCHESTRATOR_RESOURCE} to :${ORCHESTRATOR_LOCAL_PORT} ..."
kubectl port-forward -n "${NAMESPACE}" \
  "service/${ORCHESTRATOR_RESOURCE}" \
  "${ORCHESTRATOR_LOCAL_PORT}:8001" >/dev/null 2>&1 &
PORT_FORWARD_PID=$!

forward_ready=""
for _ in $(seq 1 20); do
  if curl -sf --max-time 2 "${ORCH_URL}/health" >/dev/null 2>&1; then
    forward_ready="yes"
    break
  fi
  # If the forward died (port already taken, service missing), stop rather
  # than waiting out the full timeout.
  if ! kill -0 "${PORT_FORWARD_PID}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if [ -n "${forward_ready}" ]; then
  pass "port-forward established on :${ORCHESTRATOR_LOCAL_PORT}"
else
  fail "could not reach the orchestrator on :${ORCHESTRATOR_LOCAL_PORT}."
  echo "        Is the orchestrator deployed, and is that port free? Override with"
  echo "        ORCHESTRATOR_LOCAL_PORT=<free-port> $0"
  # Kill it here rather than clearing the PID: the process may still be alive
  # and holding the port, and clearing the PID would leak it past cleanup.
  kill "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  wait "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  PORT_FORWARD_PID=""
fi

if [ -n "${forward_ready}" ]; then
  body_file="$(mktemp)"
  # shellcheck disable=SC2064
  trap "rm -f '${body_file}'; cleanup" EXIT

  http_code="$(curl -s -o "${body_file}" -w '%{http_code}' \
    --max-time 30 \
    -X POST "${ORCH_URL}/chat" \
    -H 'Content-Type: application/json' \
    -d '{"message": "Check the shipment status for order 1001 with the carrier."}' 2>/dev/null)"

  if [ "${http_code}" = "200" ]; then
    pass "POST /chat returned 200"

    if grep -q '"reply"' "${body_file}"; then
      run_id="$(sed -n 's/.*"run_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${body_file}")"
      pass "response carries a reply${run_id:+ (run_id=${run_id})}"
    else
      fail "200 response has no 'reply' field. Body:"
      sed 's/^/          /' "${body_file}"
    fi
  else
    fail "POST /chat returned HTTP ${http_code:-<none>}. Body:"
    sed 's/^/          /' "${body_file}"
  fi
fi

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

echo
if [ "${failures}" -eq 0 ]; then
  echo "PASS: observability plane removed; multi-agent core running uninterrupted in '${NAMESPACE}'."
  exit 0
fi

echo "FAIL: ${failures} check(s) did not pass — see above."
exit 1
