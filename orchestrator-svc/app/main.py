"""FastAPI application entrypoint.

Minimal skeleton: one POST /chat endpoint (placeholder echo reply) and one
GET /health endpoint, wired with request-scoped identity resolution,
structured JSON logging, and clean error handling. This is deliberately not
where agent logic lives — see app/service.py and the README for what's next
(a LangGraph orchestrator and real agents on top of this contract).
"""

from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.context import RequestContext, resolve_context
from app.logging_utils import configure_logging, log_event
from app.middleware import RequestContextMiddleware
from app.schemas import ChatRequest, ChatResponse, ErrorResponse
from app.service import generate_reply

configure_logging(settings.log_level)

app = FastAPI(title="Performance AI Backend", version="0.1.0")

# Dev-friendly default: allow all origins/methods/headers so the frontend
# (unknown origin/port for now) is never blocked by CORS. Restrict later via
# CORS_ALLOWED_ORIGINS (comma-separated) once the frontend's real origin is
# known — see app.config.Settings.cors_origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)


def _context_from_request(request: Request) -> RequestContext:
    """Fetch the context the middleware attached; generate one as a last-resort
    fallback so error paths never crash for lack of a context."""
    ctx = getattr(request.state, "context", None)
    return ctx if ctx is not None else resolve_context()


@app.get("/health")
def health() -> dict:
    # Intentionally has zero dependencies on anything else in the process
    # (no DB, no telemetry backend, no downstream service) so it can never
    # fail or hang because something else is down.
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    # No local try/except here: the global `Exception` handler below already
    # has this same context and logs a consistent shape for every endpoint,
    # not just this one — see unhandled_exception_handler.
    ctx = _context_from_request(request)
    reply = await generate_reply(payload.message, ctx)
    return ChatResponse(reply=reply, **ctx.as_dict())


def _errors_without_input(exc: RequestValidationError) -> list[dict]:
    """Strip pydantic's "input" key from each error entry before it's logged.

    FastAPI's RequestValidationError.errors() returns pre-built dicts (it
    doesn't forward include_input=False to pydantic — the underlying
    ValidationException.errors() takes no arguments), and each entry's
    "input" is the raw value that failed validation: potentially the
    caller's entire malformed body. Logging that would violate
    metadata-only-by-default, so it's dropped here rather than passed
    through to log_event.
    """
    return [{k: v for k, v in error.items() if k != "input"} for error in exc.errors()]


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    ctx = _context_from_request(request)
    log_event(
        ctx,
        "api.request.validation_error",
        endpoint=request.url.path,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        log_level=logging.WARNING,
        errors=_errors_without_input(exc),
    )
    body = ErrorResponse(
        error="invalid_request",
        detail="The request body is malformed or missing required fields (e.g. 'message').",
        **ctx.as_dict(),
    )
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=body.model_dump())


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    ctx = _context_from_request(request)
    log_event(
        ctx,
        "api.request.http_error",
        endpoint=request.url.path,
        status_code=exc.status_code,
        log_level=logging.WARNING,
        detail=str(exc.detail),
    )
    body = ErrorResponse(error="http_error", detail=str(exc.detail), **ctx.as_dict())
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # error_message/traceback are for local stdout visibility only — the
    # HTTP response below never includes them. This doesn't conflict with
    # metadata-only-by-default: that principle governs what's exported to a
    # future external telemetry system, not this process's own local logs,
    # and nothing here is exported anywhere yet.
    ctx = _context_from_request(request)
    log_event(
        ctx,
        "api.request.unhandled_exception",
        endpoint=request.url.path,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        log_level=logging.ERROR,
        error_type=type(exc).__name__,
        error_message=str(exc),
        traceback=traceback.format_exc(),
    )
    body = ErrorResponse(
        error="internal_error",
        detail="An unexpected error occurred while processing the request.",
        **ctx.as_dict(),
    )
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=body.model_dump())


if __name__ == "__main__":
    # Programmatic entrypoint so HOST/PORT from .env (app/config.py) actually
    # take effect — `uvicorn app.main:app --port ...` on the CLI silently
    # ignores settings.port; running `python -m app.main` does not. Reload is
    # tied to APP_ENV so a "development" .env still gets the usual autoreload
    # dev loop without a separate --reload flag to remember.
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.app_env == "development",
    )
