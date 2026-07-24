"""Offline validation of the Collector config (no Docker/cluster): YAML
validity, processor order, redaction/allowlist parity between the standalone
config and the k8s ConfigMap, capture-mode gating, and pinned images.

Run from orchestrator-svc/ (its venv has PyYAML + pytest):
    ./.venv/Scripts/python.exe -m pytest ../otel/tests/test_offline_config.py -v
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
STANDALONE = REPO_ROOT / "otel" / "collector-config.yaml"
CHART_DIR = REPO_ROOT / "infra" / "helm" / "observability"
COMPOSE = REPO_ROOT / "otel" / "docker-compose.otel.yml"
POLICY_DIR = REPO_ROOT / "otel" / "policy"

EXPECTED_ORDER_STANDALONE = [
    "memory_limiter", "resource", "attributes/privacy",
    "transform/limits", "filter/noise", "batch",
]
EXPECTED_ORDER_K8S = [
    "memory_limiter", "resource", "k8sattributes", "attributes/privacy",
    "transform/limits", "filter/noise", "batch",
]

def _load_standalone() -> dict:
    return yaml.safe_load(STANDALONE.read_text())

def _render_k8s_collector_config() -> dict:
    """helm template the chart, pull the otel-collector ConfigMap, parse the
    embedded collector-config.yaml as its own YAML document."""
    out = subprocess.run(
        ["helm", "template", "demo", str(CHART_DIR), "--namespace", "default"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, f"helm template failed:\n{out.stderr}"
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    cms = [d for d in docs if d.get("kind") == "ConfigMap"
           and "otel-collector" in d.get("metadata", {}).get("name", "")]
    assert len(cms) == 1, f"expected exactly one otel-collector ConfigMap, got {len(cms)}"
    embedded = cms[0]["data"]["collector-config.yaml"]
    return yaml.safe_load(embedded)

def _keep_keys(cfg: dict) -> set[str]:
    """Union of every attribute named in any keep_keys(...) — the effective
    allowlist; anything not in it is dropped before export."""
    blob = yaml.safe_dump(cfg)
    keys: set[str] = set()
    for match in re.finditer(r"keep_keys\(attributes,\s*\[(.*?)\]\)", blob, re.DOTALL):
        keys.update(re.findall(r'"([^"]+)"', match.group(1)))
    return keys

def _delete_keys(cfg: dict) -> set[str]:
    """Attribute names the attributes/privacy processor deletes outright."""
    priv = cfg["processors"]["attributes/privacy"]["actions"]
    return {a["key"] for a in priv if a.get("action") == "delete"}

# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------

def test_standalone_config_is_valid_yaml():
    cfg = _load_standalone()
    assert "receivers" in cfg and "processors" in cfg and "exporters" in cfg

def test_k8s_configmap_embedded_config_is_valid_yaml():
    cfg = _render_k8s_collector_config()
    assert "receivers" in cfg and "service" in cfg

def test_compose_file_is_valid_yaml():
    doc = yaml.safe_load(COMPOSE.read_text())
    assert "services" in doc and "otel-collector" in doc["services"]

# ---------------------------------------------------------------------------
# Processor order is exactly as documented (the "load-bearing" ordering)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pipeline", ["traces", "logs"])
def test_standalone_processor_order(pipeline):
    cfg = _load_standalone()
    procs = cfg["service"]["pipelines"][pipeline]["processors"]
    assert procs == EXPECTED_ORDER_STANDALONE, f"{pipeline}: {procs}"

@pytest.mark.parametrize("pipeline", ["traces", "logs"])
def test_k8s_processor_order(pipeline):
    cfg = _render_k8s_collector_config()
    procs = cfg["service"]["pipelines"][pipeline]["processors"]
    assert procs == EXPECTED_ORDER_K8S, f"{pipeline}: {procs}"

def test_privacy_runs_before_batch_and_exporter_in_both():
    """Redaction must run before batch/export or unredacted data can leave."""
    for cfg in (_load_standalone(), _render_k8s_collector_config()):
        for pipeline in ("traces", "logs"):
            procs = cfg["service"]["pipelines"][pipeline]["processors"]
            assert procs.index("attributes/privacy") < procs.index("batch")
            assert procs.index("transform/limits") < procs.index("batch")

def test_redaction_delete_keys_match_between_configs():
    standalone = _delete_keys(_load_standalone())
    k8s = _delete_keys(_render_k8s_collector_config())
    assert standalone == k8s, (
        f"attributes/privacy delete-lists have drifted apart.\n"
        f"standalone-only: {standalone - k8s}\nk8s-only: {k8s - standalone}"
    )

def test_allowlist_matches_between_configs():
    standalone = _keep_keys(_load_standalone())
    k8s = _keep_keys(_render_k8s_collector_config())
    assert standalone == k8s, (
        f"keep_keys allowlists have drifted apart.\n"
        f"standalone-only: {standalone - k8s}\nk8s-only: {k8s - standalone}"
    )

def test_credentials_are_deleted_in_both():
    for cfg in (_load_standalone(), _render_k8s_collector_config()):
        deleted = _delete_keys(cfg)
        for must_delete in ("http.request.header.authorization", "api_key",
                            "gen_ai.system_instructions"):
            assert must_delete in deleted, f"{must_delete} not deleted"

def _all_ottl_statements(cfg: dict) -> list[str]:
    """Every OTTL statement string across trace_statements + log_statements.
    Inspected as parsed Python strings (not re-dumped YAML) so quoting is
    exactly what the Collector will see."""
    stmts: list[str] = []
    tl = cfg["processors"]["transform/limits"]
    for group in ("trace_statements", "log_statements"):
        for ctx in tl.get(group, []):
            stmts.extend(ctx.get("statements", []))
    return stmts

def test_content_attributes_are_capture_mode_gated_in_both():
    for cfg in (_load_standalone(), _render_k8s_collector_config()):
        stmts = _all_ottl_statements(cfg)
        for attr in ("gen_ai.input.messages", "gen_ai.output.messages"):
            gate = (
                f'delete_key(attributes, "{attr}") '
                f'where attributes["capture.mode"] != "content-approved"'
            )
            assert gate in stmts, f"{attr} is not capture.mode-gated (looked for: {gate})"
        # …but they ARE in the allowlist, so content-approved can keep them.
        assert "gen_ai.input.messages" in _keep_keys(cfg)
        assert "gen_ai.output.messages" in _keep_keys(cfg)

def _image_refs(node) -> list[str]:
    """Recursively collect actual image references from parsed YAML:
    compose-style `image: repo:tag` strings and chart-style
    `image: {repository, tag}` maps. Parsing (not raw-text scanning) so a
    comment mentioning 'latest' is never mistaken for a real tag."""
    refs: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "image" and isinstance(value, str):
                refs.append(value)
            elif key == "image" and isinstance(value, dict) and "tag" in value:
                refs.append(f"{value.get('repository', '')}:{value['tag']}")
            else:
                refs.extend(_image_refs(value))
    elif isinstance(node, list):
        for item in node:
            refs.extend(_image_refs(item))
    return refs

def test_no_latest_image_tags_anywhere():
    for path in (COMPOSE, CHART_DIR / "values.yaml"):
        for ref in _image_refs(yaml.safe_load(path.read_text())):
            assert not ref.endswith(":latest") and not ref.endswith(":"), (
                f"{path} uses an unpinned/latest image tag: {ref!r}"
            )
            assert ":" in ref, f"{path} image has no explicit tag: {ref!r}"

def test_collector_image_tag_matches_between_compose_and_chart():
    compose = yaml.safe_load(COMPOSE.read_text())
    compose_img = compose["services"]["otel-collector"]["image"]
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
    chart_repo = values["otelCollector"]["image"]["repository"]
    chart_tag = values["otelCollector"]["image"]["tag"]
    assert compose_img == f"{chart_repo}:{chart_tag}", (
        f"compose uses {compose_img} but chart uses {chart_repo}:{chart_tag} — "
        f"the two Collector deployments should run the same pinned build"
    )

# ---------------------------------------------------------------------------
# Policy files conform to the schema's shape (no jsonschema dependency)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["metadata-only", "content-approved"])
def test_policy_file_declares_matching_capture_mode(mode):
    doc = yaml.safe_load((POLICY_DIR / f"{mode}.yaml").read_text())
    assert doc["captureMode"] == mode

def test_policy_schema_enumerates_exactly_the_two_modes():
    import json
    schema = json.loads((POLICY_DIR / "policy.schema.json").read_text())
    enum = schema["properties"]["captureMode"]["enum"]
    assert set(enum) == {"metadata-only", "content-approved"}
