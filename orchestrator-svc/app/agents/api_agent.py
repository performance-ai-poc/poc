"""REST API Agent.

STUB — replace with real agent logic. This module currently simulates a
successful external API call (e.g. a shipment-status lookup) so the
orchestrator graph in app/orchestrator/ is independently buildable and
testable before the real REST API Agent lands.
"""

from __future__ import annotations

# TEST-ONLY, not demo/production behavior — see db_agent.FORCE_FAILURE_TRIGGER
# for the full rationale. Checked against the raw message so a multi-step
# plan can force failure specifically on this agent's step.
FORCE_FAILURE_TRIGGER = "__FORCE_API_AGENT_FAILURE__"


def api_agent_node(state):
    step = state["steps"][state["current_step"]]

    if FORCE_FAILURE_TRIGGER in state["message"]:
        state["step_results"][step["key"]] = {
            "status": "error",
            "summary": "",
            "duration_ms": 10,
            "error": "simulated_tool_timeout",
        }
        return state

    # STUB — replace with real agent logic (external REST API call).
    state["step_results"][step["key"]] = {
        "status": "success",
        "summary": f"[API Agent stub] Retrieved shipment status for: {step['instruction']}",
        "duration_ms": 95,
    }
    return state
