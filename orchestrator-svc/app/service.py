"""Chat business logic.

Wires the /chat endpoint to the deterministic LangGraph orchestrator in
app/orchestrator/. See docs/orchestration-routing-implementation-doc.md,
Section 9. This replaces the placeholder echo reply; no other part of
main.py, middleware.py, schemas.py, or the /chat contract changes.
"""

from __future__ import annotations

from app.context import RequestContext
from app.orchestrator.graph import compiled_graph
from app.orchestrator.state import RunState


async def generate_reply(message: str, ctx: RequestContext) -> str:
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
    final_state = await compiled_graph.ainvoke(initial_state)
    return final_state["answer"]
