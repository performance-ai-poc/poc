# Orchestrator Tests

Pytest coverage for the FastAPI orchestrator contract.

Run from `orchestrator-svc/`:

```bash
python -m pytest
```

Current tests cover:

- `GET /health`
- successful `POST /chat`
- generated and caller-supplied IDs
- ignored caller-supplied `run_id` and `request_id`
- validation errors
- oversized bodies
- log redaction expectations

No external server is required; tests use FastAPI's in-process `TestClient`.
