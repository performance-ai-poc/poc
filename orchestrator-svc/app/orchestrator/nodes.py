"""LangGraph node functions for the orchestrator's deterministic routing graph.

See docs/orchestration-routing-implementation-doc.md, Section 7.

Every log line emitted here goes through the existing, already-tested
``log_event`` (via the new ``log_agent_step_*`` wrappers in
``app.logging_utils``) and always carries ``state["ctx"]`` — the same
``RequestContext`` object threaded through the whole request — so the four
correlation IDs are never re-derived or duplicated here.

Only digests of instruction text are ever logged, never the raw text itself
(coding convention, Section 8).
"""

from __future__ import annotations

import hashlib

from app.logging_utils import log_agent_step_completed, log_agent_step_failed, log_agent_step_started

from .routing import build_step_plan
from .state import is_run_finished


def _digest(text: str) -> str:
    """Stable, non-reversible digest for logging — never log raw text."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def route_node(state):
    if not state.get("steps"):
        state["steps"] = build_step_plan(state["message"])
        state["current_step"] = 0

    # advance_node's "advance" -> "route" edge re-enters this node after
    # every step, including the last one. pick_agent (the conditional edge
    # run immediately after this node returns) is what actually decides to
    # go to "assemble" once current_step reaches the end of the plan, or
    # once a prior step has set "aborted" — but that check happens *after*
    # this function body runs, so this node must guard the same condition
    # itself before indexing into steps, or it raises IndexError on the
    # final re-entry of every request (and would log a spurious
    # step_started for a step that abort-on-first-failure is about to skip).
    # is_run_finished() is the single shared source of truth for this guard —
    # see app.orchestrator.state — so this check can never drift out of sync
    # with pick_agent's identical check in app/orchestrator/routing.py.
    if is_run_finished(state):
        return state

    step = state["steps"][state["current_step"]]
    log_agent_step_started(
        state["ctx"],
        {
            "graph.node": "route",
            "step.sequence": step["sequence"],
            "agent": step["agent"],
            "instruction_digest": _digest(step["instruction"]),
        },
    )
    return state


def advance_node(state):
    idx = state["current_step"]
    step = state["steps"][idx]
    result = state["step_results"].get(step["key"], {})
    success = result.get("status") == "success"

    # Step.status (Section 4's TypedDict) is "success" | "error", distinct
    # from the "completed" | "failed" event-name suffix logged below.
    step["status"] = "success" if success else "error"

    payload = {
        "graph.node": "advance",
        "step.sequence": step["sequence"],
        "duration_ms": result.get("duration_ms"),
        "agent": step["agent"],
    }
    if success:
        payload["app.outcome"] = "success"
        log_agent_step_completed(state["ctx"], payload)
    else:
        # Metadata-only: error_type is a short category/reason string (e.g.
        # "simulated_tool_failure"), never a raw exception or traceback.
        error_type = result.get("error") or "unknown_error"
        state["errors"].append({
            "step_key": step["key"],
            "agent": step["agent"],
            "step.sequence": step["sequence"],
            "error_type": error_type,
        })
        # Abort-on-first-failure: pick_agent/route_node check this on the
        # next "route" re-entry and skip straight to "done" instead of
        # dispatching any remaining steps.
        state["aborted"] = True
        payload["error_type"] = error_type
        payload["app.outcome"] = "failed"
        payload["app.failure.category"] = error_type
        log_agent_step_failed(state["ctx"], payload)

    state["current_step"] += 1
    return state


def assemble_node(state):
    completed_summaries = [
        state["step_results"][s["key"]]["summary"]
        for s in state["steps"]
        if s["status"] == "success"
    ]

    if state["errors"]:
        failure_notes = [
            f"step {err['step.sequence']} failed: {err['error_type']}"
            for err in state["errors"]
        ]
        parts = completed_summaries + [
            "Run stopped early - " + "; ".join(failure_notes) + "."
        ]
        state["answer"] = " ".join(filter(None, parts))
        state["status"] = "failed"
    else:
        state["answer"] = " ".join(filter(None, completed_summaries)) or "No results found."
        state["status"] = "completed"
    return state
