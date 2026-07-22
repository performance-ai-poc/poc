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

    # ------------------------------------------------------------------
    # REST API Agent (Agent 2) — LLM + MCP wiring.
    #
    # The sub-agents are the ONLY place an LLM is called (the orchestrator
    # itself is deterministic — see the architecture spec, section 3.2), so
    # these settings are consumed by app/agents/api_agent.py via app/llm.py
    # and app/mcp_client.py, never by the graph/routing layer.
    #
    # `agent_live_calls` is the master switch between two behaviours that
    # emit IDENTICAL telemetry (the agent.* log contract in section 5):
    #   - False (default): fully in-process, offline, deterministic. No
    #     network. This is what the test suite and any environment without
    #     the LLM/MCP services running rely on.
    #   - True: real HTTP calls to the OpenAI-compatible LLM endpoint and
    #     real streamable-http calls to the MCP server. Set this in the
    #     demo/deploy environment (.env, Docker, Helm) once both are up.
    # ------------------------------------------------------------------
    agent_live_calls: bool = False

    # OpenAI-compatible chat-completions endpoint the REST API Agent's
    # planner calls. Only used when agent_live_calls is True.
    llm_base_url: str = "https://openai.rc.asu.edu/v1"
    llm_api_key: str | None = None
    llm_model: str = "qwen3-coder-30b-a3b-instruct"
    # Seconds before a single LLM HTTP attempt is abandoned (the retry helper
    # governs tool calls; this bounds the planner call specifically). The demo
    # model (qwen3-coder-30b-a3b-instruct) is a non-reasoning instruct model
    # that returns in ~2-4s, so this is a generous ceiling that also fails
    # within a minute if the endpoint is unreachable/hung.
    llm_timeout_s: float = 60.0

    # streamable-http URL of the MCP server (mcp-server/app/server.py, which
    # binds :8000). Only used when agent_live_calls is True.
    mcp_server_url: str = "http://localhost:8000/mcp"
    mcp_timeout_s: float = 30.0

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
