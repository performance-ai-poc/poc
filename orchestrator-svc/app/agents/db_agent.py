"""DB Agent.

STUB — replace with real agent logic. This module currently simulates a
successful database lookup (and, per the implementation doc, document
search folds into this agent as a tool call rather than a separate agent)
so the orchestrator graph in app/orchestrator/ is independently buildable
and testable before the real DB Agent lands.
"""

from __future__ import annotations

from app.telemetry import trace_agent_step

# TEST-ONLY, not demo/production behavior: with no real DB Agent or MCP
# server to inject a genuine failure into yet, this trigger phrase lets
# tests deliberately force this stub to fail so the orchestrator's
# abort-on-first-failure path (app/orchestrator/nodes.py) can be exercised
# end-to-end. Checked against the raw message (not the canned per-rule
# instruction text) so a single /chat call can still produce a multi-step
# plan while forcing this specific step to fail. Never triggered by demo
# traffic — the trigger string is not an ordinary English phrase.
FORCE_FAILURE_TRIGGER = "__FORCE_DB_AGENT_FAILURE__"


@trace_agent_step
def db_agent_node(state):
    step = state["steps"][state["current_step"]]

    if FORCE_FAILURE_TRIGGER in state["message"]:
        state["step_results"][step["key"]] = {
            "status": "error",
            "summary": "",
            "duration_ms": 10,
            "error": "simulated_tool_failure",
        }
        return state

    # STUB — replace with real agent logic (DB lookup / document search).
    state["step_results"][step["key"]] = {
        "status": "success",
        "summary": f"[DB Agent stub] Found matching records for: {step['instruction']}",
        "duration_ms": 120,
    }
    return state
