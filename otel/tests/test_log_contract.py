"""Contract test for what orchestrator-svc logs vs. what the filelog receiver
and the allowlist in otel/collector-config.yaml expect. Drives the real app
and checks its output, so a new unallowlisted field or a changed timestamp
format fails here rather than silently producing empty telemetry live.

Run from orchestrator-svc/:
    ./.venv/Scripts/python.exe -m pytest ../otel/tests/test_log_contract.py -v
"""

from __future__ import annotations

import datetime
import io
import json
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.logging_utils import _JsonFormatter, configure_logging, get_logger
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
STANDALONE = REPO_ROOT / "otel" / "collector-config.yaml"

# Envelope fields the filelog receiver handles specially (timestamp is promoted
# to the record's native Timestamp then removed; service.name is a resource
# attribute; event is what filter/noise keys on) rather than passing through
# the keep_keys allowlist as an ordinary attribute.
ENVELOPE_FIELDS = {"timestamp", "service.name", "event"}

RENAME_SOURCE_FIELDS = {
    "agent",          # -> gen_ai.agent.name
    "error_type",     # -> error.type
    "model_id",       # -> gen_ai.request.model
    "input_tokens",   # -> gen_ai.usage.input_tokens
    "output_tokens",  # -> gen_ai.usage.output_tokens
    "tool_name",      # -> gen_ai.tool.name
    "endpoint",       # -> http.route (api.request.* only)
}

client = TestClient(app)

def _allowlist() -> set[str]:
    blob = STANDALONE.read_text()
    keys: set[str] = set()
    for match in re.finditer(r"keep_keys\(attributes,\s*\[(.*?)\]\)", blob, re.DOTALL):
        keys.update(re.findall(r'"([^"]+)"', match.group(1)))
    return keys

def _timestamp_layout() -> str:
    """The strptime layout the filelog json_parser is configured with."""
    blob = STANDALONE.read_text()
    m = re.search(r"layout:\s*'([^']+)'", blob)
    assert m, "could not find a timestamp layout in the filelog receiver config"
    return m.group(1)

def _all_emitted_lines(message: str) -> list[dict]:
    """Drive a real /chat request and return every structured log line it
    emitted — exactly what the container runtime would write to
    /var/log/pods/.../*.log for the filelog receiver to read."""
    configure_logging("INFO")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        resp = client.post("/chat", json={"message": message})
        assert resp.status_code == 200
    finally:
        logger.removeHandler(handler)
    return [json.loads(ln) for ln in stream.getvalue().splitlines() if ln.strip()]

# A single message that exercises the widest field set: routes to api_agent
# (llm_call + tool events) AND db_agent (step events) in one run.
WIDE_MESSAGE = "Check my orders and the shipment status with the carrier, and the escalation policy."

def test_every_emitted_line_is_single_line_json():
    """The filelog json_parser assumes one JSON object per line. A multi-line
    or non-JSON line would be dropped by filter/noise (no `event`), so this
    guards the receiver's core assumption against the real emitter."""
    configure_logging("INFO")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        client.post("/chat", json={"message": WIDE_MESSAGE})
    finally:
        logger.removeHandler(handler)
    for line in stream.getvalue().splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)  # raises if any line isn't standalone JSON
        assert isinstance(obj, dict)

def test_timestamp_format_matches_the_receiver_layout():
    """Every emitted timestamp must parse under the exact strptime layout the
    filelog receiver is configured with. If the app changes its timestamp
    format, the receiver would silently fail to parse it."""
    layout = _timestamp_layout()
    for line in _all_emitted_lines(WIDE_MESSAGE):
        ts = line["timestamp"]
        # Python's strptime is the reference here; see the KNOWN-RISK note below
        # for why stanza (Go) may still differ on the colon-offset.
        datetime.datetime.strptime(ts, layout)

def test_timestamp_is_rfc3339_colon_offset_KNOWN_RISK():
    """The app emits `...+00:00` (colon in the UTC offset). Python's %z accepts
    it; stanza's strptime %z historically may not. This test documents the
    format precisely so that if the first live run shows no logs ingested, the
    timestamp layout is the first place to look (see
    otel/VERIFICATION_STATUS.md). It is a canary, not a failure of our config
    against Python.
    """
    for line in _all_emitted_lines(WIDE_MESSAGE):
        assert re.search(r"[+-]\d{2}:\d{2}$", line["timestamp"]), (
            "timestamp no longer carries a colon UTC offset — if the app moved "
            "to `+0000`, update the receiver layout note accordingly"
        )

def test_no_emitted_field_is_silently_dropped_by_the_allowlist():
    """THE integration assertion. Collect every attribute the app emits across a
    wide run; every one must either be in the Collector's keep_keys allowlist or
    be an envelope field handled separately. A field the app emits that is in
    neither would be silently discarded before export — telemetry the app took
    the trouble to produce, lost with no error anywhere."""
    allow = _allowlist()
    emitted: set[str] = set()
    for line in _all_emitted_lines(WIDE_MESSAGE):
        emitted.update(line.keys())

    dropped = emitted - allow - ENVELOPE_FIELDS - RENAME_SOURCE_FIELDS
    assert not dropped, (
        f"these fields are emitted by orchestrator-svc but are NOT in the "
        f"Collector allowlist, not envelope fields, and not rename sources — "
        f"they would be silently dropped: {sorted(dropped)}. "
        f"Add them to keep_keys in otel/collector-config.yaml (and the k8s "
        f"ConfigMap), or confirm they are intentionally dropped."
    )

def test_correlation_ids_present_on_every_line():
    """Every record must carry the four IDs (the whole correlation story rests
    on them) plus the envelope — matching what the receiver promotes and what
    SEMCONV.md keeps as always-present attributes."""
    for line in _all_emitted_lines(WIDE_MESSAGE):
        for key in ("run_id", "request_id", "session_id", "tenant_id",
                    "service.name", "event"):
            assert key in line, f"{line.get('event')} missing {key}"

def test_service_name_is_the_value_the_mapping_mirrors():
    """service.name must be exactly what SEMCONV.md/HANDOFF.md record as the
    (open, unresolved) emitted value, so the naming-conflict decision stays a
    one-place change. If the app changes it, that decision surfaces here."""
    lines = _all_emitted_lines(WIDE_MESSAGE)
    names = {ln["service.name"] for ln in lines}
    assert names == {"backend-api"}, (
        f"service.name changed to {names} — update otel/HANDOFF.md's naming "
        f"section; the Collector passes it through verbatim so nothing breaks, "
        f"but the open decision's premise moved"
    )

def test_gen_ai_mapping_source_fields_are_actually_emitted():
    """SEMCONV.md maps model_id/tool_name/agent/error_type/input_tokens/
    output_tokens onto gen_ai.* names. Confirm the app really emits those raw
    fields, so the mapping has something to rename rather than being dead OTTL."""
    emitted: set[str] = set()
    # include a failure to get error_type on agent.step_failed
    for msg in (WIDE_MESSAGE,
                "Check my orders __FORCE_DB_AGENT_FAILURE__"):
        for line in _all_emitted_lines(msg):
            emitted.update(line.keys())
    for raw in ("model_id", "tool_name", "agent", "error_type",
                "input_tokens", "output_tokens"):
        assert raw in emitted, (
            f"SEMCONV.md maps '{raw}' but the app never emits it in these runs — "
            f"the mapping rule for it is currently dead"
        )
