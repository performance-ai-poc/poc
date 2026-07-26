import type { DashboardData } from "./types";

// Served behind nginx, which proxies /api/analytics/ to the analytics service.
// A relative path means the same build works in the cluster and degrades
// gracefully in local `vite dev` (where the fetch simply fails and the UI keeps
// its fallback data).
const DASHBOARD_URL = "/api/analytics/dashboard";

export async function fetchDashboardData(signal?: AbortSignal): Promise<DashboardData> {
  const resp = await fetch(DASHBOARD_URL, { signal });
  if (!resp.ok) {
    throw new Error(`dashboard fetch failed: ${resp.status}`);
  }
  return (await resp.json()) as DashboardData;
}
