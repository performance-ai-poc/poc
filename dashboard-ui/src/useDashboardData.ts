import { useEffect, useState } from "react";
import { fetchDashboardData } from "./api";
import { mockDashboardData } from "./mockData";
import type { DashboardData } from "./types";

// Provides the dashboard data, live from the analytics service, refreshed on an
// interval so drift gauges move as new telemetry arrives. Fails open: it starts
// from the bundled mock so the page always renders, and on any fetch error it
// keeps the last good data rather than blanking out. The analytics endpoint is
// itself fail-open, so a degraded backend surfaces as `unavailable` tiles, not
// a broken page.
export function useDashboardData(pollMs = 15000): DashboardData {
  const [data, setData] = useState<DashboardData>(mockDashboardData);

  useEffect(() => {
    const controller = new AbortController();

    const load = async () => {
      try {
        const live = await fetchDashboardData(controller.signal);
        setData(live);
      } catch {
        // Keep whatever we already have (mock on first load, last-good after).
      }
    };

    load();
    const id = window.setInterval(load, pollMs);
    return () => {
      controller.abort();
      window.clearInterval(id);
    };
  }, [pollMs]);

  return data;
}
