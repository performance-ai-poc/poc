"""Test-suite-wide defaults.

app.config now loads .env into the process environment (so the OTEL_* block
that .env.example documents actually reaches the OpenTelemetry SDK, which reads
os.environ directly). A side effect is that the test suite would otherwise
inherit whatever endpoint a developer has configured and start exporting real
spans to it — polluting the telemetry backend with test data, and adding
network timeouts to every run.

Tests assert on the app's own structured log output, never on exported
telemetry, so disable the SDK before any test module imports app.main (which
calls configure_telemetry at import time). setdefault, not a hard assignment,
so a run can still opt in explicitly.
"""

from __future__ import annotations

import os

os.environ.setdefault("OTEL_SDK_DISABLED", "true")
