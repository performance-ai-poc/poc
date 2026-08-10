"""FastAPI entrypoint for the analytics service.

Read-only drift/analytics plane in front of the telemetry backend. It exposes:
  GET  /health        - liveness, zero dependencies
  GET  /dashboard     - the dashboard payload, computed on demand (poll path)
  WS   /ws/dashboard  - the same payload pushed live from a warm cache

Behind the dashboard nginx it is reached as /api/analytics/*. It is
deliberately a separate process from orchestrator-svc so a slow or failing
drift query can never touch the chat request path.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.dashboard import _all_unavailable
from app.dashboard import router as dashboard_router
from app.live import cache, refresher

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger("analytics.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run the background dashboard refresher for the lifetime of the app, and
    cancel it cleanly on shutdown."""
    task = asyncio.create_task(refresher())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Performance AI Analytics", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    # Zero dependencies on the telemetry backend, so liveness never depends on
    # whether OpenObserve is reachable, so the dashboard degrades to unavailable
    # tiles instead of the service reporting itself unhealthy.
    return {"status": "ok", "service": "analytics-svc"}


@app.websocket("/ws/dashboard")
async def ws_dashboard(websocket: WebSocket) -> None:
    """Push the latest dashboard snapshot to the client on an interval.

    Reads the warm cache the refresher keeps updated, so this handler does no
    querying itself. Before the first compute lands, it sends an all-unavailable
    payload so the client always has a valid, correctly-shaped message.
    """
    await websocket.accept()
    try:
        while True:
            data = cache.get() or _all_unavailable()
            await websocket.send_text(data.model_dump_json())
            await asyncio.sleep(settings.ws_push_seconds)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - a dead client must never crash the app
        logger.info("ws_dashboard connection closed with error", exc_info=True)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


app.include_router(dashboard_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level=settings.log_level.lower())
