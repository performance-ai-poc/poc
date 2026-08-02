"""Tests for the /health and /chat contract.

Run with `python -m pytest` (or plain `pytest`) from the backend/ directory —
see pytest.ini, which puts backend/ on sys.path so `app.main` resolves
regardless of how pytest is invoked.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

import httpx
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.context import ID_MAX_LENGTH
from app.logging_utils import _JsonFormatter, get_logger
from app.main import app
from app.middleware import MAX_BODY_BYTES

client = TestClient(app)


def _capture_logs():
    """Returns (stream, handler) — caller attaches/detaches around a request
    the same way every other log-capturing test in this suite does."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    return stream, handler


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_with_valid_message_returns_reply_and_all_four_ids():
    response = client.post("/chat", json={"message": "hello"})
    assert response.status_code == 200

    body = response.json()
    # The reply now comes from the orchestrator graph (stub agents), not a
    # fixed echo string, so this only checks a reply was actually produced.
    assert isinstance(body.get("reply"), str) and body["reply"]
    for key in ("run_id", "request_id", "session_id", "tenant_id"):
        assert isinstance(body.get(key), str) and body[key]


def test_chat_echoes_caller_supplied_session_id():
    response = client.post("/chat", json={"message": "hi", "session_id": "my-session-123"})
    assert response.status_code == 200
    assert response.json()["session_id"] == "my-session-123"


def test_chat_ignores_caller_supplied_run_id_and_request_id():
    response = client.post(
        "/chat",
        json={"message": "hi", "run_id": "spoofed-run-id", "request_id": "spoofed-request-id"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["run_id"] != "spoofed-run-id"
    assert body["request_id"] != "spoofed-request-id"


def test_chat_missing_message_returns_422():
    response = client.post("/chat", json={})
    assert response.status_code == 422


def test_chat_oversized_session_id_returns_422():
    response = client.post(
        "/chat",
        json={"message": "hi", "session_id": "a" * (ID_MAX_LENGTH + 1)},
    )
    assert response.status_code == 422


def test_chat_oversized_body_returns_413():
    oversized_message = "a" * (MAX_BODY_BYTES + 1)
    response = client.post("/chat", json={"message": oversized_message})
    assert response.status_code == 413


def test_chat_oversized_message_returns_422():
    response = client.post("/chat", json={"message": "a" * 10_001})
    assert response.status_code == 422


def test_malformed_body_does_not_leak_raw_content_into_logs():
    """A body that fails validation at the whole-body level (e.g. a bare
    JSON string instead of an object) must never have its raw content
    written to logs, even though pydantic's error objects normally carry
    an "input" key with exactly that content. See main.validation_exception_handler.
    """
    marker = "SECRET_MARKER_VALUE"

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        response = client.post(
            "/chat",
            content=json.dumps(marker),
            headers={"Content-Type": "application/json"},
        )
    finally:
        logger.removeHandler(handler)

    # The caller still gets a clean, helpful validation error.
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"

    logged_output = stream.getvalue()
    assert marker not in logged_output
    assert marker not in response.text


def test_valid_chat_message_does_not_leak_into_logs():
    """A normal, successful /chat request must never have its message content
    written to logs — only the four identifiers, endpoint, timestamp, and
    status/event metadata. See app/logging_utils.py and app/service.py."""
    marker = "SECRET_MARKER_VALUE"

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        response = client.post("/chat", json={"message": marker})
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    # The reply now comes from the orchestrator graph (stub agents), not a
    # fixed echo string, so this only checks a reply was actually produced —
    # the marker is expected to appear in the reply body itself (it's the
    # actual answer content, not a log line), just never in the logs below.
    assert isinstance(response.json().get("reply"), str) and response.json()["reply"]

    logged_output = stream.getvalue()
    assert marker not in logged_output


# ---------------------------------------------------------------------------
# Audit follow-up: streaming body-cap enforcement without Content-Length
# ---------------------------------------------------------------------------


def test_oversized_body_without_content_length_still_rejected_by_streaming_cap():
    """test_chat_oversized_body_returns_413 above sends a body with a normal
    Content-Length header, which only exercises the cheap short-circuit in
    RequestContextMiddleware._read_body_capped (middleware.py). This test
    forces httpx to omit Content-Length entirely (by passing a generator,
    which has no known length up front) so the actual defense — counting
    bytes off the ASGI stream as they arrive — is what has to catch the
    oversized body.
    """
    oversized_message = "a" * (MAX_BODY_BYTES + 1)
    body_bytes = json.dumps({"message": oversized_message}).encode()

    def _chunks():
        chunk_size = 4096
        for i in range(0, len(body_bytes), chunk_size):
            yield body_bytes[i : i + chunk_size]

    response = client.post(
        "/chat",
        content=_chunks(),
        headers={"Content-Type": "application/json"},
    )

    assert "content-length" not in {h.lower() for h in response.request.headers}
    assert response.status_code == 413


# ---------------------------------------------------------------------------
# Audit follow-up: /health and CORS preflight bypass the context middleware,
# so neither should ever produce an api.request.* log line.
# ---------------------------------------------------------------------------


def test_health_produces_no_request_log_lines():
    stream, handler = _capture_logs()
    logger = get_logger()
    logger.addHandler(handler)
    try:
        response = client.get("/health")
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    assert stream.getvalue().strip() == ""


def test_options_preflight_produces_no_request_log_lines():
    stream, handler = _capture_logs()
    logger = get_logger()
    logger.addHandler(handler)
    try:
        response = client.options(
            "/chat",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    assert stream.getvalue().strip() == ""


def test_cors_header_present_on_real_chat_response_not_just_preflight():
    """test_options_preflight_produces_no_request_log_lines above only checks
    that the OPTIONS preflight itself is quiet. It does not confirm CORS
    headers actually reach a real cross-origin request's response — this
    test drives an actual POST /chat with an Origin header and checks
    Access-Control-Allow-Origin is present on the real response, per
    app.main's CORSMiddleware configuration (default allow_origins=["*"]).
    """
    response = client.post(
        "/chat",
        json={"message": "hello"},
        headers={"Origin": "http://example.com"},
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Audit follow-up: omitted tenant_id falls back to settings.default_tenant_id
# in both the response body and every logged line for that request.
# ---------------------------------------------------------------------------


def test_chat_without_tenant_id_uses_default_tenant_in_response_and_logs():
    stream, handler = _capture_logs()
    logger = get_logger()
    logger.addHandler(handler)
    try:
        response = client.post("/chat", json={"message": "hello"})
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    assert response.json()["tenant_id"] == settings.default_tenant_id

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    assert lines, "expected at least one log line for this request"
    assert all(line["tenant_id"] == settings.default_tenant_id for line in lines)


# ---------------------------------------------------------------------------
# Audit follow-up: two concurrent /chat requests must not cross-contaminate
# IDs or results. Uses httpx.AsyncClient over an ASGITransport (in-process,
# no real socket) rather than pytest-asyncio, since that plugin isn't a
# project dependency — asyncio.run() inside an ordinary sync test function
# is enough to drive real concurrent coroutines through the same
# module-level compiled_graph singleton (app/orchestrator/graph.py).
# ---------------------------------------------------------------------------


def test_concurrent_chat_requests_do_not_cross_contaminate():
    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            return await asyncio.gather(
                ac.post("/chat", json={"message": "Can you look up my orders?", "session_id": "session-A"}),
                ac.post("/chat", json={"message": "Check shipment status please", "session_id": "session-B"}),
            )

    response_a, response_b = asyncio.run(_run())

    assert response_a.status_code == 200
    assert response_b.status_code == 200

    body_a, body_b = response_a.json(), response_b.json()
    assert body_a["session_id"] == "session-A"
    assert body_b["session_id"] == "session-B"
    assert body_a["run_id"] != body_b["run_id"]
    assert body_a["request_id"] != body_b["request_id"]
    # The agents strip the canned per-rule directive before they work
    # (app/agents/db/routing.py::_PARENT_SQL_DIRECTIVE), so each reply carries
    # that agent's own summary rather than the routing sentence. Session A
    # asked about orders and must come back with the DB agent's record
    # summary; session B asked about shipments and must come back with the API
    # agent's shipment summary. Crucially, neither may carry the other's —
    # that is what cross-contamination would look like here.
    assert "matching record" in body_a["reply"]
    assert "shipment status" not in body_a["reply"].lower()
    assert "shipment status" in body_b["reply"].lower()
    assert "matching record" not in body_b["reply"]


# ---------------------------------------------------------------------------
# Audit follow-up: an all-whitespace message passes schemas.py's min_length=1
# (whitespace characters count) but is semantically empty. This locks in
# current behavior — the fallback routing path handles it like any other
# non-matching message — rather than leaving it untested.
# ---------------------------------------------------------------------------


def test_all_whitespace_message_is_accepted_and_routed_via_fallback():
    response = client.post("/chat", json={"message": "   "})

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("reply"), str) and body["reply"]


# ---------------------------------------------------------------------------
# Audit follow-up: a malformed body that parses as valid JSON but isn't a
# JSON object at all (e.g. a bare array) must be handled the same cleanly as
# the bare-JSON-string case above — middleware._peek_ids's
# `isinstance(data, dict)` guard should reject it without raising, falling
# through to pydantic's own validation error for the real 422.
# ---------------------------------------------------------------------------


def test_malformed_array_body_does_not_crash_and_returns_422():
    response = client.post(
        "/chat",
        content=json.dumps([1, 2, 3]),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_request"
    for key in ("run_id", "request_id", "session_id", "tenant_id"):
        assert isinstance(body.get(key), str) and body[key]


# ---------------------------------------------------------------------------
# Audit follow-up: Settings.cors_origins parsing — comma-separated string ->
# list, and "*" handled as the special all-origins case rather than being
# parsed as a literal one-item list containing the string "*"... which is
# actually what CORSMiddleware expects too, so this also documents that
# "*" happens to survive as ["*"] either way (see app/config.py).
# ---------------------------------------------------------------------------


def test_cors_origins_parses_comma_separated_list():
    parsed = Settings(cors_allowed_origins="https://a.example.com, https://b.example.com ,https://c.example.com").cors_origins
    assert parsed == ["https://a.example.com", "https://b.example.com", "https://c.example.com"]


def test_cors_origins_star_is_special_cased_to_allow_all():
    assert Settings(cors_allowed_origins="*").cors_origins == ["*"]
    # Whitespace around the lone "*" is still recognized as the special case.
    assert Settings(cors_allowed_origins="  *  ").cors_origins == ["*"]


def test_cors_origins_ignores_blank_entries_in_comma_separated_list():
    parsed = Settings(cors_allowed_origins="https://a.example.com,, https://b.example.com,").cors_origins
    assert parsed == ["https://a.example.com", "https://b.example.com"]
