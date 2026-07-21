"""Application configuration.

All configurable values are read from the environment (optionally via a
``.env`` file loaded through pydantic-settings). Nothing is hardcoded here —
this is the seam a future model API key or other secret plugs into without
touching application code.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8001
    log_level: str = "INFO"

    # Placeholder for when real agent logic is wired in; unused today and
    # never logged or echoed back.
    model_api_key: str | None = None

    # Fallback tenant_id used whenever a caller doesn't supply one (see
    # app.context.resolve_context). A stable default instead of a fresh
    # random UUID per request, so anonymous/demo traffic groups under one
    # consistent tenant for correlation and analytics instead of each
    # request becoming its own one-off "tenant". Still unauthenticated —
    # see the README's tenant_id limitation note.
    default_tenant_id: str = "default-tenant"

    # Comma-separated list of allowed CORS origins, or "*" for all origins
    # (dev-friendly default). Tighten this once the frontend's real origin
    # is known, e.g. CORS_ALLOWED_ORIGINS=https://app.example.com.
    cors_allowed_origins: str = "*"

    @property
    def cors_origins(self) -> list[str]:
        if self.cors_allowed_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


settings = Settings()
