"""Analytics service configuration.

All values come from the environment (optionally via a .env file). The service
is read-only: it queries the telemetry backend and computes drift. It never
writes to any system it observes.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8002
    log_level: str = "INFO"

    # OpenObserve query API. Same backend the Collector exports to and the
    # otel/tests query against. Auth is a read-only Basic credential injected
    # from a secret in deployment; the default here is local-only and harmless.
    openobserve_url: str = "http://localhost:5080"
    openobserve_org: str = "default"
    openobserve_stream: str = "default"
    openobserve_auth: str = (
        "Basic YWRtaW5AZXhhbXBsZS10ZXN0LmludmFsaWQ6b3RlbC1wb2MtbG9jYWwtb25seQ=="
    )
    openobserve_timeout_s: float = 10.0

    # Drift windows. The live window is the most recent `live_window_minutes`;
    # the baseline is the `baseline_window_minutes` immediately before it. Drift
    # is how much the live window's distribution has moved from that baseline.
    live_window_minutes: int = 15
    baseline_window_minutes: int = 60

    # The Collector's memory_limiter ceiling, used to turn the Collector's raw
    # RSS self-metric into the Memory tile's percent. Must match the limit set
    # in the collector config / Helm values (512 MiB there).
    collector_memory_limit_mib: int = 512

    # Comma-separated allowed CORS origins, or "*". The dashboard is served from
    # its own nginx origin and proxies here, so "*" is the dev-friendly default.
    cors_allowed_origins: str = "*"

    @property
    def cors_origins(self) -> list[str]:
        raw = self.cors_allowed_origins.strip()
        if raw == "*" or not raw:
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()
