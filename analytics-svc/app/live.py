"""In-memory latest-dashboard cache and the background task that keeps it fresh.

Real-time pushes want the newest dashboard every second, but computing it hits
OpenObserve and takes longer than that. So one background task recomputes on an
interval and stores the result here; every WebSocket client then reads this
cache instead of triggering its own query. One compute serves all clients, and
the push rate is fully decoupled from the query time.

The compute (build_dashboard) is synchronous and does blocking HTTP, so it is
run in a worker thread to keep the event loop free for the socket pushes.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.dashboard import build_dashboard
from app.schemas import DashboardData

logger = logging.getLogger("analytics.live")


class DashboardCache:
    """Holds the most recently computed dashboard snapshot. `None` until the
    first successful compute; callers fall back to an all-unavailable payload
    in that window so a client is never left without a valid message."""

    def __init__(self) -> None:
        self._data: DashboardData | None = None

    def get(self) -> DashboardData | None:
        return self._data

    def set(self, data: DashboardData) -> None:
        self._data = data


# Module-level singleton, shared by the refresher task and the WebSocket handler.
cache = DashboardCache()


async def refresher() -> None:
    """Recompute the dashboard on an interval and store it in the cache.

    Never raises out of the loop: any compute error is logged and the last good
    snapshot is kept, matching the fail-open discipline of the rest of the
    service. Cancelled cleanly on shutdown.
    """
    logger.info(
        "dashboard refresher starting (every %.1fs)", settings.dashboard_refresh_seconds
    )
    while True:
        try:
            data = await asyncio.to_thread(build_dashboard)
            cache.set(data)
        except asyncio.CancelledError:
            logger.info("dashboard refresher stopping")
            raise
        except Exception:  # noqa: BLE001 - keep the loop alive no matter what
            logger.exception("dashboard refresh failed; keeping last snapshot")
        await asyncio.sleep(settings.dashboard_refresh_seconds)
