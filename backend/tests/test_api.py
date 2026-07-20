"""Tests for the /health and /chat contract.

Run with `python -m pytest` (or plain `pytest`) from the backend/ directory —
see pytest.ini, which puts backend/ on sys.path so `app.main` resolves
regardless of how pytest is invoked.
"""

from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient

from app.context import ID_MAX_LENGTH
from app.logging_utils import _JsonFormatter, get_logger
from app.main import app
from app.middleware import MAX_BODY_BYTES

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_with_valid_message_returns_reply_and_all_four_ids():
    response = client.post("/chat", json={"message": "hello"})
    assert response.status_code == 200

    body = response.json()
    assert body["reply"] == "Received: hello"
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
    assert response.json()["reply"] == f"Received: {marker}"

    logged_output = stream.getvalue()
    assert marker not in logged_output
