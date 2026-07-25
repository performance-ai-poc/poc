"""Captures the real spans a /chat request produces (in-memory, no Collector)
and checks every attribute is allowlisted, a normalized correlation source, or
an intentionally-deleted sensitive key — so nothing the app emits is silently
dropped and no sensitive key survives.

Run from orchestrator-svc/:
    ./.venv/Scripts/python.exe -m pytest ../otel/tests/test_span_contract.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.main import app  # triggers configure_telemetry (SDK provider, no exporter)

REPO_ROOT = Path(__file__).resolve().parents[2]
STANDALONE = REPO_ROOT / "otel" / "collector-config.yaml"

# app.<id> correlation attributes are normalized to bare run_id/... on spans by
# transform/limits before the allowlist runs, so the app.<id> forms are
# intentionally dropped afterward.
NORMALIZED_SOURCES = {"app.run_id", "app.request_id", "app.session_id", "app.tenant_id"}

client = TestClient(app)

_exporter = InMemorySpanExporter()
_provider = trace.get_tracer_provider()

def _trace_allowlist() -> set[str]:
    """Attributes kept on spans: the keep_keys list inside trace_statements."""
    cfg = yaml.safe_load(STANDALONE.read_text())
    stmts = []
    for ctxblock in cfg["processors"]["transform/limits"].get("trace_statements", []):
        stmts.extend(ctxblock.get("statements", []))
    keys: set[str] = set()
    for stmt in stmts:
        for m in re.finditer(r"keep_keys\(attributes,\s*\[(.*?)\]\)", stmt, re.DOTALL):
            keys.update(re.findall(r'"([^"]+)"', m.group(1)))
    return keys

def _deleted_keys() -> set[str]:
    cfg = yaml.safe_load(STANDALONE.read_text())
    return {a["key"] for a in cfg["processors"]["attributes/privacy"]["actions"]
            if a.get("action") == "delete"}

@pytest.fixture(scope="module", autouse=True)
def _capture_spans():
    if not hasattr(_provider, "add_span_processor"):
        pytest.skip("active TracerProvider is a no-op (import order); run this module alone")
    _provider.add_span_processor(SimpleSpanProcessor(_exporter))
    yield

def _emitted_span_attributes(message: str) -> set[str]:
    _exporter.clear()
    resp = client.post("/chat", json={"message": message})
    assert resp.status_code == 200
    attrs: set[str] = set()
    for span in _exporter.get_finished_spans():
        attrs.update(span.attributes.keys())
    return attrs

WIDE = "Check my orders and the shipment status with the carrier, and the escalation policy."

def test_app_actually_emits_spans():
    attrs = _emitted_span_attributes(WIDE)
    assert attrs, "no span attributes captured — the SDK produced no spans"

def test_no_span_attribute_is_silently_dropped_by_the_allowlist():
    allow = _trace_allowlist()
    emitted = _emitted_span_attributes(WIDE)
    dropped = emitted - allow - NORMALIZED_SOURCES - _deleted_keys()
    assert not dropped, (
        f"these span attributes are emitted by the app SDK but are NOT in the "
        f"Collector trace allowlist and would be silently dropped: {sorted(dropped)}. "
        f"Add them to the trace keep_keys in otel/collector-config.yaml AND the "
        f"k8s ConfigMap, or confirm they are intentionally dropped."
    )

def test_core_gen_ai_semconv_attributes_are_present_and_allowlisted():
    """The attributes the dashboard actually needs must both be emitted by the
    app and survive the allowlist."""
    allow = _trace_allowlist()
    emitted = _emitted_span_attributes(WIDE)
    for attr in ("gen_ai.operation.name", "gen_ai.agent.name",
                 "gen_ai.request.model", "gen_ai.usage.input_tokens",
                 "gen_ai.usage.output_tokens", "gen_ai.tool.name"):
        assert attr in emitted, f"app SDK did not emit {attr} on any span"
        assert attr in allow, f"{attr} emitted but not allowlisted — would be dropped"

def test_correlation_ids_are_normalized_onto_spans_and_kept():
    """The app sets app.run_id etc.; the Collector normalizes to bare run_id and
    keeps those. Confirm the app really emits the app.<id> source form."""
    emitted = _emitted_span_attributes(WIDE)
    assert "app.run_id" in emitted, "app SDK did not set app.run_id on spans"
    for bare in ("run_id", "request_id", "session_id", "tenant_id"):
        assert bare in _trace_allowlist(), f"{bare} not in trace allowlist"

def test_raw_sql_key_is_in_the_delete_list_not_the_allowlist():
    """Raw SQL keys must be deleted, never allowlisted."""
    deleted = _deleted_keys()
    allow = _trace_allowlist()
    for sql_key in ("db.statement", "db.query.text"):
        assert sql_key in deleted, f"{sql_key} not deleted — raw SQL could leak"
        assert sql_key not in allow, f"{sql_key} must not be allowlisted"
