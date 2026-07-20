"""FastAPI application entrypoint.

Minimal skeleton: one POST /chat endpoint (placeholder echo reply) and one
GET /health endpoint, wired with request-scoped identity resolution,
structured JSON logging, and clean error handling. This is deliberately not
where agent logic lives — see app/service.py and the README for what's next
(a LangGraph orchestrator and real agents on top of this contract).
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.context import RequestContext, resolve_context
from app.logging_utils import configure_logging, log_event
from app.middleware import RequestContextMiddleware
from app.schemas import ChatRequest, ChatResponse
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
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    ctx = _context_from_request(request)
    try:
        reply = generate_reply(payload.message, ctx)
    except Exception:
        log_event(ctx, "chat.unhandled_error", endpoint="/chat", level=40)
        raise
    return ChatResponse(reply=reply, **ctx.as_dict())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    ctx = _context_from_request(request)
    log_event(
        ctx,
        "request.validation_error",
        endpoint=request.url.path,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        level=30,
        errors=exc.errors(),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "invalid_request",
            "detail": "The request body is malformed or missing required fields (e.g. 'message').",
            **ctx.as_dict(),
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    ctx = _context_from_request(request)
    log_event(
        ctx,
        "request.http_error",
        endpoint=request.url.path,
        status_code=exc.status_code,
        level=30,
        detail=str(exc.detail),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "http_error", "detail": str(exc.detail), **ctx.as_dict()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    ctx = _context_from_request(request)
    log_event(
        ctx,
        "request.unhandled_exception",
        endpoint=request.url.path,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        level=40,
        error_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_error",
            "detail": "An unexpected error occurred while processing the request.",
            **ctx.as_dict(),
        },
    )
