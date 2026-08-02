"""Test-suite-wide defaults.

Settings reads the OTEL_* block straight out of .env (app/config.py), and
telemetry.py drives the exporters off those values. That is the point of the
fix, but it also means the test suite inherits whatever endpoint a developer
has configured: every run then ships test spans to that collector and pays the
retry timeouts when nothing is listening.

Tests assert on the app's own structured log output, never on exported
telemetry, so disable the SDK before any test module imports app.main (which
calls configure_telemetry at import time). An environment variable is what does
it — pydantic-settings ranks env vars above .env, so this wins over the file.
setdefault, not a hard assignment, so a run can still opt in explicitly.
"""

from __future__ import annotations

import os

os.environ.setdefault("OTEL_SDK_DISABLED", "true")
