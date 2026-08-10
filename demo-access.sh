#!/usr/bin/env bash
#
# Bulletproof demo access. The ingress is not wired (no controller), so the
# reliable way to reach the UIs is port-forwarding. This opens all the tunnels
# the demo needs and prints the URLs. Leave it running in its own terminal;
# press Ctrl+C to close every tunnel at once.
#
#   Chat UI       -> http://localhost:8080
#   Dashboard UI  -> http://localhost:8090
#   Analytics API -> http://localhost:8002/dashboard   (raw JSON, optional)
#   OpenObserve   -> http://localhost:5080             (telemetry backend, optional)

set -uo pipefail

NS="${NAMESPACE:-default}"

pids=()
forward() {
  local svc="$1" local_port="$2" target_port="$3"
  kubectl port-forward -n "${NS}" "service/${svc}" "${local_port}:${target_port}" >/dev/null 2>&1 &
  pids+=("$!")
}

cleanup() {
  echo ""
  echo "Closing tunnels..."
  for pid in "${pids[@]}"; do kill "${pid}" 2>/dev/null || true; done
}
trap cleanup EXIT INT TERM

echo "Starting port-forwards (namespace: ${NS})..."
forward demo-ai-chat-customer-ui   8080 80
forward observability-dashboard-ui 8090 80
forward observability-analytics    8002 8002
forward observability-openobserve  5080 5080

sleep 5

echo ""
echo "========================================================"
echo "  Chat UI       ->  http://localhost:8080"
echo "  Dashboard UI  ->  http://localhost:8090"
echo "  Analytics API ->  http://localhost:8002/dashboard"
echo "  OpenObserve   ->  http://localhost:5080"
echo "========================================================"
echo ""

ok=0
for pf in "8080 chat-ui" "8090 dashboard-ui" "8002 analytics" "5080 openobserve"; do
  set -- $pf
  if curl -sf -o /dev/null --max-time 5 "http://localhost:$1/" 2>/dev/null \
     || curl -sf -o /dev/null --max-time 5 "http://localhost:$1/health" 2>/dev/null \
     || curl -sf -o /dev/null --max-time 5 "http://localhost:$1/healthz" 2>/dev/null; then
    echo "  ready: $2 (localhost:$1)"; ok=$((ok+1))
  else
    echo "  NOT ready yet: $2 (localhost:$1) - give it a few seconds"
  fi
done
echo ""
echo "${ok}/4 tunnels responding. Leave this terminal open. Ctrl+C to stop."
echo ""

# Hold the tunnels open until interrupted.
wait
