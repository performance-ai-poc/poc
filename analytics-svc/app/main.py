"""FastAPI entrypoint for the analytics service.

Read-only drift/analytics plane in front of the telemetry backend. It exposes:
  GET /health     - liveness, zero dependencies
  GET /dashboard  - the dashboard payload (added in the dashboard module)

Behind the dashboard nginx it is reached as /api/analytics/*. It is
deliberately a separate process from orchestrator-svc so a slow or failing
drift query can never touch the chat request path.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.dashboard import router as dashboard_router

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

app = FastAPI(title="Performance AI Analytics", version="0.1.0")

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


app.include_router(dashboard_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level=settings.log_level.lower())
