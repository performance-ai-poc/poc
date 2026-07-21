# Orchestrator App

FastAPI application package for `orchestrator-svc`.

Key files:

- `main.py` - FastAPI app, routes, exception handlers, and uvicorn entrypoint.
- `schemas.py` - public request/response models.
- `context.py` - request identity generation and validation.
- `middleware.py` - request context middleware, body-size guard, and access logging.
- `service.py` - placeholder business logic for `POST /chat`.
- `logging_utils.py` - structured JSON stdout logging.
- `config.py` - environment-driven settings.

The important local convention is that request context is passed explicitly
through function calls and into logs. Avoid adding request-related behavior
that drops `run_id`, `request_id`, `session_id`, or `tenant_id`.
