# Orchestrator App

FastAPI application package for `orchestrator-svc`.

Key files:

- `main.py` - FastAPI app, routes, exception handlers, and uvicorn entrypoint.
- `schemas.py` - public request/response models.
- `context.py` - request identity generation and validation.
- `middleware.py` - request context middleware, body-size guard, and access logging.
- `service.py` - wires `POST /chat` to the LangGraph orchestrator.
- `logging_utils.py` - structured JSON stdout logging + the `agent.*` emitters.
- `config.py` - environment-driven settings.
- `orchestrator/` - deterministic LangGraph graph, routing, nodes, and shared state.
- `agents/` - the executor sub-agents: `db_agent.py` (stub) and `api_agent.py`
  (REST API Agent, fully implemented per spec section 4.5).
- `retry.py` - the single shared retry helper wrapping every MCP tool call;
  owns the `agent.tool_selected` / `agent.retried` / `agent.tool_returned` events.
- `llm.py` - the REST API Agent's LLM planner (OpenAI-compatible; live/offline).
- `mcp_client.py` - streamable-http MCP transport (live/offline) for tool calls.

The important local convention is that request context is passed explicitly
through function calls and into logs. Avoid adding request-related behavior
that drops `run_id`, `request_id`, `session_id`, or `tenant_id`.
