"""Test-suite-wide defaults for the otel/ contract tests.

These run against orchestrator-svc (from that directory, so `app` is
importable), but they live outside its rootdir and so do not pick up
orchestrator-svc/conftest.py. Settings reads the OTEL_* block straight out of
.env, so without this, importing app.main configures a live exporter and ships
test spans to whatever collector is running.

Unlike orchestrator-svc/conftest.py this must NOT set OTEL_SDK_DISABLED: these
tests build real spans (via their own TracerProvider + InMemorySpanExporter) to
assert on span shape and trace propagation, and disabling the SDK makes them
produce nothing.

Clearing the endpoint from os.environ does not work either — Settings just
falls back to the value in .env, which is the whole point of reading it there.
Selecting the `none` exporter is what survives: telemetry.py then installs no
exporter for any signal while the SDK itself stays enabled. Env vars outrank
.env in pydantic-settings, so these win over the file.
"""

from __future__ import annotations

import os

for _signal in ("TRACES", "METRICS", "LOGS"):
    os.environ[f"OTEL_{_signal}_EXPORTER"] = "none"
