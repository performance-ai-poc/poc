"""Test-suite-wide defaults for the otel/ contract tests.

These run against orchestrator-svc (from that directory, so `app` is
importable), but they live outside its rootdir and so do not pick up
orchestrator-svc/conftest.py. app.config loads .env into the process
environment, so without this the OTEL_* block in a developer's .env would make
importing app.main configure a live OTLP exporter and ship test spans to
whatever collector happens to be running.

Unlike orchestrator-svc/conftest.py this must NOT set OTEL_SDK_DISABLED: these
tests build real spans (via their own TracerProvider + InMemorySpanExporter) to
assert on span shape and trace propagation, and disabling the SDK makes them
produce nothing.

Deleting the endpoint from os.environ does not work either — app.config's
load_dotenv(override=False) simply reads it back out of .env, since popping it
is exactly what makes it "not already set". Selecting the `none` exporter is
what survives: app.telemetry then installs no exporter for any signal while the
SDK itself stays enabled.
"""

from __future__ import annotations

import os

for _signal in ("TRACES", "METRICS", "LOGS"):
    os.environ[f"OTEL_{_signal}_EXPORTER"] = "none"
