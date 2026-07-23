#!/usr/bin/env bash
# Acceptance A14 (docs/ACCEPTANCE.md): the Collector's ServiceAccount has
# get/list/watch only. No create/update/patch/delete/exec. No Secrets
# access. All host mounts readOnly: true.
#
# Two modes:
#   - static (always available, no cluster needed): renders the chart with
#     `helm template` and inspects the ClusterRole + DaemonSet directly.
#     This is what ran during this workstream's build (Docker/cluster were
#     unavailable — see otel/VERIFICATION_STATUS.md) and is genuine
#     verification of the rendered manifests, just not of how a real
#     Kubernetes API server enforces them.
#   - live (if `kubectl cluster-info` succeeds): additionally runs
#     `kubectl auth can-i` as the Collector's ServiceAccount to confirm the
#     API server itself denies the disallowed verbs, not just that the
#     YAML never asks for them.
#
# Usage: ./otel/tests/test_rbac.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART_DIR="${REPO_ROOT}/infra/helm/ai-chat"
RELEASE="${RELEASE:-demo}"
NAMESPACE="${NAMESPACE:-default}"
RENDERED_FILE="$(mktemp)"
trap 'rm -f "${RENDERED_FILE}"' EXIT

# Prefer `python`, not `python3` — on Windows, `python3` on PATH is
# frequently the Microsoft Store install-stub alias, not a real
# interpreter, and `command -v` alone can't tell the difference.
PYTHON_BIN=""
for candidate in python python3; do
  if "${candidate}" -c "import yaml" >/dev/null 2>&1; then
    PYTHON_BIN="${candidate}"
    break
  fi
done
if [ -z "${PYTHON_BIN}" ]; then
  echo "FAIL: no working Python with PyYAML found (tried python, python3)."
  exit 1
fi

echo "==> Rendering chart (static check — no cluster required)"
if ! helm template "${RELEASE}" "${CHART_DIR}" --namespace "${NAMESPACE}" > "${RENDERED_FILE}" 2>&1; then
  echo "FAIL: helm template failed:"
  cat "${RENDERED_FILE}"
  exit 1
fi

CHECK_SCRIPT="$(mktemp --suffix=.py)"
trap 'rm -f "${RENDERED_FILE}" "${CHECK_SCRIPT}"' EXIT

cat > "${CHECK_SCRIPT}" <<'PYEOF'
import sys, yaml

with open(sys.argv[1], "r", encoding="utf-8") as f:
    docs = [d for d in yaml.safe_load_all(f) if d]

cluster_roles = [d for d in docs if d.get("kind") == "ClusterRole" and "otel-collector" in d.get("metadata", {}).get("name", "")]
daemonsets = [d for d in docs if d.get("kind") == "DaemonSet" and "otel-collector" in d.get("metadata", {}).get("name", "")]

fail = False

if not cluster_roles:
    print("FAIL: no otel-collector ClusterRole found in rendered output.")
    sys.exit(1)

FORBIDDEN_VERBS = {"create", "update", "patch", "delete", "exec", "deletecollection"}
ALLOWED_VERBS = {"get", "list", "watch"}

for cr in cluster_roles:
    for rule in cr.get("rules", []):
        verbs = set(rule.get("verbs", []))
        bad = verbs & FORBIDDEN_VERBS
        if bad:
            print(f"FAIL: ClusterRole {cr['metadata']['name']} grants forbidden verb(s) {bad} on {rule.get('resources')}")
            fail = True
        extra = verbs - ALLOWED_VERBS
        if extra:
            print(f"FAIL: ClusterRole {cr['metadata']['name']} grants unexpected verb(s) {extra} (only get/list/watch expected)")
            fail = True
        if "secrets" in [str(r).lower() for r in rule.get("resources", [])]:
            print(f"FAIL: ClusterRole {cr['metadata']['name']} grants access to Secrets.")
            fail = True
    all_verbs = sorted(set(v for r in cr.get("rules", []) for v in r.get("verbs", [])))
    print(f"    OK: ClusterRole {cr['metadata']['name']} - verbs only {all_verbs}")

if not daemonsets:
    print("FAIL: no otel-collector DaemonSet found in rendered output.")
    sys.exit(1)

for ds in daemonsets:
    pod_spec = ds["spec"]["template"]["spec"]
    pod_sc = pod_spec.get("securityContext", {})
    if not pod_sc.get("runAsNonRoot"):
        print(f"FAIL: DaemonSet {ds['metadata']['name']} pod securityContext missing runAsNonRoot: true")
        fail = True

    volumes = {v["name"]: v for v in pod_spec.get("volumes", [])}
    for c in pod_spec.get("containers", []):
        c_sc = c.get("securityContext", {})
        if c_sc.get("allowPrivilegeEscalation") is not False:
            print(f"FAIL: container {c['name']} missing allowPrivilegeEscalation: false")
            fail = True
        caps = c_sc.get("capabilities", {}).get("drop", [])
        if "ALL" not in caps:
            print(f"FAIL: container {c['name']} does not drop ALL capabilities (drop: {caps})")
            fail = True

        for vm in c.get("volumeMounts", []):
            vol = volumes.get(vm["name"])
            if vol and "hostPath" in vol:
                if not vm.get("readOnly"):
                    print(f"FAIL: hostPath volume '{vm['name']}' ({vol['hostPath']['path']}) is mounted WITHOUT readOnly: true")
                    fail = True
                else:
                    print(f"    OK: hostPath volume '{vm['name']}' ({vol['hostPath']['path']}) is readOnly: true")

if fail:
    sys.exit(1)
print("PASS (static): RBAC verbs, Secrets exclusion, non-root/no-priv-esc/no-capabilities, and read-only host mounts all confirmed in the rendered manifests.")
PYEOF

"${PYTHON_BIN}" "${CHECK_SCRIPT}" "${RENDERED_FILE}"
static_status=$?

if [ ${static_status} -ne 0 ]; then
  exit 1
fi

if kubectl cluster-info >/dev/null 2>&1; then
  echo "==> Live cluster detected — checking with kubectl auth can-i"
  SA="system:serviceaccount:${NAMESPACE}:${RELEASE}-ai-chat-otel-collector"
  for verb_resource in "create pods" "delete pods" "patch pods" "get secrets" "list secrets" "create clusterroles"; do
    verb=$(echo "${verb_resource}" | cut -d' ' -f1)
    resource=$(echo "${verb_resource}" | cut -d' ' -f2)
    result=$(kubectl auth can-i "${verb}" "${resource}" --as="${SA}" 2>/dev/null)
    if [ "${result}" = "yes" ]; then
      echo "FAIL (live): ${SA} unexpectedly CAN ${verb} ${resource}"
      exit 1
    fi
    echo "    OK (live): ${SA} cannot ${verb} ${resource}"
  done
  echo "PASS (live): API server itself denies every disallowed verb."
else
  echo "==> No live cluster reachable (kubectl cluster-info failed) — static check above is all that ran."
  echo "    See otel/VERIFICATION_STATUS.md."
fi

exit 0
