"""Graph topology for the orchestrator's deterministic routing layer.

See docs/orchestration-routing-implementation-doc.md, Section 5.

There is no retry edge in this graph. If a sub-agent returns a terminal
failure, ``advance_node`` marks the step failed and control still proceeds
(to the next step, or to ``assemble`` once steps are exhausted) — all
retrying happens inside a sub-agent, via a shared retry helper, before
control returns to ``advance``. This graph never loops back to re-attempt a
step.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agents.api_agent import api_agent_node
from app.agents.db_agent import db_agent_node

from .nodes import advance_node, assemble_node, route_node
from .routing import pick_agent
from .state import RunState

graph = StateGraph(RunState)
graph.add_node("route", route_node)
graph.add_node("db_agent", db_agent_node)
graph.add_node("api_agent", api_agent_node)
graph.add_node("advance", advance_node)
graph.add_node("assemble", assemble_node)

graph.set_entry_point("route")
graph.add_conditional_edges(
    "route",
    pick_agent,
    {"db": "db_agent", "api": "api_agent", "done": "assemble"},
)
graph.add_edge("db_agent", "advance")
graph.add_edge("api_agent", "advance")
graph.add_edge("advance", "route")
graph.add_edge("assemble", END)

compiled_graph = graph.compile()
