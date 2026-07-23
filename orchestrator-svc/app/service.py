"""Chat business logic.

Wires the /chat endpoint to the deterministic LangGraph orchestrator in
app/orchestrator/. See docs/orchestration-routing-implementation-doc.md,
Section 9. This replaces the placeholder echo reply; no other part of
main.py, middleware.py, schemas.py, or the /chat contract changes.
"""

from __future__ import annotations

import time

from app.context import RequestContext
from app.orchestrator.graph import compiled_graph
from app.orchestrator.state import RunState
from app.telemetry import context_attributes, mark_failure, record_error, tracer, workflow_count, workflow_duration


async def generate_reply(message: str, ctx: RequestContext) -> str:
    started = time.perf_counter()
    workflow_count.add(1, {"workflow": "performance_ai_chat"})
    with tracer.start_as_current_span(
        "invoke_workflow",
        attributes={
            **context_attributes(ctx),
            "gen_ai.operation.name": "invoke_workflow",
            "gen_ai.workflow.name": "performance_ai_chat",
        },
    ) as span:
        initial_state: RunState = {
            "ctx": ctx,
            "message": message,
            "config": {},
            "steps": [],
            "current_step": 0,
            "step_results": {},
            "errors": [],
            "aborted": False,
            "answer": None,
            "status": "running",
        }
        try:
            final_state = await compiled_graph.ainvoke(initial_state)
            span.set_attribute("app.outcome", final_state["status"])
            if final_state["status"] == "failed":
                mark_failure(span, "workflow_failed")
            return final_state["answer"]
        except Exception as exc:
            record_error(span, exc)
            raise
        finally:
            workflow_duration.record(
                time.perf_counter() - started,
                {"workflow": "performance_ai_chat"},
            )
