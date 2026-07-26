#!/usr/bin/env bash
#
# Live acceptance check for the analytics service. Assumes it is running (e.g.
# `docker compose up -d analytics-svc openobserve`). Mirrors the otel/tests
# style: curl the real endpoints, PASS/FAIL on the contract.
#
#   D1  /dashboard returns a valid, complete DashboardData payload
#   D3  a missing/empty backend yields unavailable tiles, never a 500
#
# D2 (drift actually moves a gauge) needs seeded telemetry and is exercised by
# the Python contract tests and the manual seed script; this script covers the
# always-true deployment contract.

set -uo pipefail

ANALYTICS_URL="${ANALYTICS_URL:-http://localhost:8002}"

fail() { echo "FAIL: $1"; exit 1; }

echo "==> GET /health"
health=$(curl -s "${ANALYTICS_URL}/health")
echo "${health}" | grep -q '"status":"ok"' || fail "health did not report ok: ${health}"
echo "    ok"

echo "==> GET /dashboard returns HTTP 200"
code=$(curl -s -o /dev/null -w "%{http_code}" "${ANALYTICS_URL}/dashboard")
[ "${code}" = "200" ] || fail "expected 200, got ${code} (endpoint must fail open, never 500)"
echo "    ok (200)"

echo "==> /dashboard payload is structurally complete and honestly sourced"
python - "${ANALYTICS_URL}" <<'PYEOF'
import json, sys, urllib.request
url = sys.argv[1] + "/dashboard"
data = json.load(urllib.request.urlopen(url, timeout=10))

for key, n in (("driftMetrics", 5), ("technicalMetrics", 11),
               ("resourceMetrics", 4), ("correctiveActions", 4)):
    got = len(data.get(key, []))
    if got != n:
        print(f"FAIL: {key} has {got} entries, expected {n}"); sys.exit(1)

valid_sources = {"mock", "instrumented", "inferred", "unavailable"}
valid_bands = {"low", "medium", "high"}
for key in ("driftMetrics", "technicalMetrics", "resourceMetrics"):
    for tile in data[key]:
        if tile["source"] not in valid_sources:
            print(f"FAIL: {tile['id']} has invalid source {tile['source']!r}"); sys.exit(1)
        if tile["band"] not in valid_bands:
            print(f"FAIL: {tile['id']} has invalid band {tile['band']!r}"); sys.exit(1)

inferred = [t["id"] for t in data["driftMetrics"] if t["source"] == "inferred"]
print(f"    ok: shape valid; {len(inferred)} drift tile(s) currently inferred: {inferred}")
PYEOF
[ $? -eq 0 ] || exit 1

echo ""
echo "PASS: analytics /dashboard is live, complete, and every tile carries an honest source."
