import { useEffect, useState } from "react";
import { fetchDashboardData } from "./api";
import { mockDashboardData } from "./mockData";
import type { DashboardData } from "./types";

// Live dashboard data. Prefers a WebSocket (/api/analytics/ws/dashboard) so
// gauges update in real time as telemetry arrives, and falls back automatically
// to polling the REST endpoint if the socket is unavailable or drops. Fails
// open at every layer: it starts from the bundled mock so the page always
// renders, keeps the last good data on any error, and the analytics endpoint is
// itself fail-open so a degraded backend surfaces as `unavailable` tiles.
function dashboardWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/analytics/ws/dashboard`;
}

export function useDashboardData(pollMs = 15000): DashboardData {
  const [data, setData] = useState<DashboardData>(mockDashboardData);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let pollTimer: number | null = null;
    let reconnectTimer: number | null = null;
    let unmounted = false;
    let lastJson = "";
    const controller = new AbortController();

    // Apply a raw JSON snapshot, skipping frames identical to the last one so
    // an unchanged payload never triggers a re-render (keeps the gauges steady).
    const apply = (raw: string) => {
      if (raw === lastJson) return;
      lastJson = raw;
      try {
        setData(JSON.parse(raw) as DashboardData);
      } catch {
        // ignore a malformed frame; keep the last good data
      }
    };

    const poll = async () => {
      try {
        const live = await fetchDashboardData(controller.signal);
        apply(JSON.stringify(live));
      } catch {
        // keep last-good data
      }
    };
    const startPolling = () => {
      if (pollTimer !== null) return;
      poll();
      pollTimer = window.setInterval(poll, pollMs);
    };
    const stopPolling = () => {
      if (pollTimer !== null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const connect = () => {
      if (unmounted) return;
      let socket: WebSocket;
      try {
        socket = new WebSocket(dashboardWsUrl());
      } catch {
        startPolling();
        return;
      }
      ws = socket;
      socket.onopen = () => {
        stopPolling(); // the socket is now the live source
      };
      socket.onmessage = (ev) => apply(ev.data as string);
      socket.onclose = () => {
        if (ws === socket) ws = null;
        if (unmounted) return;
        startPolling(); // fall back to polling immediately
        if (reconnectTimer === null) {
          reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            connect(); // then try the socket again
          }, 3000);
        }
      };
      socket.onerror = () => {
        // onclose fires next and handles fallback + reconnect
      };
    };

    startPolling(); // data flows instantly, even before the socket opens
    connect();

    return () => {
      unmounted = true;
      controller.abort();
      stopPolling();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (ws) {
        ws.onclose = null; // don't trigger reconnect on unmount
        ws.close();
      }
    };
  }, [pollMs]);

  return data;
}
