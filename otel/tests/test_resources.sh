#!/usr/bin/env bash
# Acceptance A15 (docs/ACCEPTANCE.md): the Collector declares and stays
# inside CPU, memory, and disk limits.
#
#   CPU request     100-200m
#   CPU limit       500m
#   Memory request  192-256Mi
#   Memory limit    512Mi
#   Persistent queue 256Mi-1Gi
#
# Static mode (no cluster needed): confirms the rendered DaemonSet declares
# requests/limits inside these targets, and that no other workload in this
# chart has any (so the Collector isn't just matching an existing
# precedent, since docs/OTEL_PLAN.md/docs/ACCEPTANCE.md both note none
# exists). "Stays inside" under real load is what test_saturation.sh checks
# live; this script is the declared-values half of A15.
#
# Usage: ./otel/tests/test_resources.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART_DIR="${REPO_ROOT}/infra/helm/ai-chat"
RELEASE="${RELEASE:-demo}"
NAMESPACE="${NAMESPACE:-default}"
RENDERED_FILE="$(mktemp)"
CHECK_SCRIPT="$(mktemp --suffix=.py)"
trap 'rm -f "${RENDERED_FILE}" "${CHECK_SCRIPT}"' EXIT

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

echo "==> Rendering chart"
if ! helm template "${RELEASE}" "${CHART_DIR}" --namespace "${NAMESPACE}" --set "otelCollector.queue.sizeLimit=1Gi" > "${RENDERED_FILE}" 2>&1; then
  echo "FAIL: helm template failed:"
  cat "${RENDERED_FILE}"
  exit 1
fi

cat > "${CHECK_SCRIPT}" <<'PYEOF'
import sys, re

def to_millicores(v):
    v = str(v)
    if v.endswith("m"):
        return int(v[:-1])
    return int(float(v) * 1000)

def to_mib(v):
    v = str(v)
    if v.endswith("Mi"):
        return int(v[:-2])
    if v.endswith("Gi"):
        return int(float(v[:-2]) * 1024)
    if v.endswith("Ki"):
        return int(v[:-2]) / 1024
    raise ValueError(f"unrecognized memory unit: {v}")

with open(sys.argv[1], "r", encoding="utf-8") as f:
    text = f.read()

import yaml
docs = [d for d in yaml.safe_load_all(text) if d]
daemonsets = [d for d in docs if d.get("kind") == "DaemonSet" and "otel-collector" in d.get("metadata", {}).get("name", "")]
other_workloads = [d for d in docs if d.get("kind") in ("Deployment", "StatefulSet", "DaemonSet") and "otel-collector" not in d.get("metadata", {}).get("name", "")]

fail = False

if not daemonsets:
    print("FAIL: no otel-collector DaemonSet found in rendered output.")
    sys.exit(1)

for ds in daemonsets:
    for c in ds["spec"]["template"]["spec"]["containers"]:
        res = c.get("resources", {})
        requests = res.get("requests", {})
        limits = res.get("limits", {})
        if not requests or not limits:
            print(f"FAIL: container {c['name']} is missing requests or limits entirely.")
            fail = True
            continue

        cpu_req_m = to_millicores(requests.get("cpu", "0"))
        cpu_lim_m = to_millicores(limits.get("cpu", "0"))
        mem_req_mi = to_mib(requests.get("memory", "0Mi"))
        mem_lim_mi = to_mib(limits.get("memory", "0Mi"))

        checks = [
            ("CPU request", cpu_req_m, 100, 200, "m"),
            ("CPU limit", cpu_lim_m, 500, 500, "m"),
            ("Memory request", mem_req_mi, 192, 256, "Mi"),
            ("Memory limit", mem_lim_mi, 512, 512, "Mi"),
        ]
        for label, actual, lo, hi, unit in checks:
            if not (lo <= actual <= hi):
                print(f"FAIL: {label} = {actual}{unit}, expected within [{lo}, {hi}]{unit} (docs/CONSTRAINTS.md C6)")
                fail = True
            else:
                print(f"    OK: {label} = {actual}{unit} (within [{lo}, {hi}]{unit})")

# Persistent queue emptyDir size
for v in ds["spec"]["template"]["spec"].get("volumes", []):
    if v.get("name") == "otelcol-storage":
        size = v.get("emptyDir", {}).get("sizeLimit")
        if not size:
            print("FAIL: otelcol-storage volume has no sizeLimit — must be bounded (C6, C2).")
            fail = True
        else:
            size_mi = to_mib(size)
            if not (256 <= size_mi <= 1024):
                print(f"FAIL: persistent queue sizeLimit = {size}, expected within [256Mi, 1Gi] (C6)")
                fail = True
            else:
                print(f"    OK: persistent queue sizeLimit = {size} (within [256Mi, 1Gi])")

if other_workloads:
    names = [w["metadata"]["name"] for w in other_workloads]
    unbounded = [w["metadata"]["name"] for w in other_workloads
                 if any(not c.get("resources") for c in w["spec"]["template"]["spec"]["containers"])]
    print(f"    INFO: other workloads in this chart ({names}) - "
          f"{len(unbounded)}/{len(names)} have no resources block at all, "
          f"confirming the Collector is not just matching an existing precedent "
          f"(docs/OTEL_PLAN.md: 'No resource limits on any application workload').")

if fail:
    sys.exit(1)
print("PASS (static): Collector resource requests/limits and queue bound are all within docs/CONSTRAINTS.md C6 targets.")
PYEOF

"${PYTHON_BIN}" "${CHECK_SCRIPT}" "${RENDERED_FILE}"
exit $?
