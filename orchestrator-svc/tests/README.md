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
- orchestrator routing: keyword-match edge cases, word-boundary correctness,
  rule ordering, casing, and the no-match fallback
  (`test_orchestrator_routing.py`)
- the `agent.*` log event schema is flat, not nested under a "payload" key
  (`test_log_event_schema.py`)
- the abort-on-first-failure path: aborted steps, `state["errors"]`,
  `agent.step_failed` error detail, and the assembled failure answer
  (`test_orchestrator_failure_path.py`)

No external server is required; tests use FastAPI's in-process `TestClient`.
