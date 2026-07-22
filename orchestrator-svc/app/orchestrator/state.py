"""Shared LangGraph state for the orchestrator's deterministic routing graph.

See docs/orchestration-routing-implementation-doc.md, Section 4.

``schema_cache`` is intentionally not part of this state: schema is fetched
once per request by the DB Agent when needed, with no session-scoped caching
in this version. ``ctx`` reuses the existing, already-tested
``RequestContext`` object built at the API layer rather than duplicating its
four fields into a separate dict, so there is one source of truth for
correlation IDs across the whole request lifecycle.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from app.context import RequestContext


class Step(TypedDict):
    key: str
    agent: str  # "db_agent" | "api_agent"
    instruction: str
    sequence: int
    status: Literal["pending", "success", "error"]


class RunState(TypedDict):
    ctx: RequestContext
    message: str
    config: dict  # model, max_retries, allow_writes
    steps: list[Step]
    current_step: int
    step_results: dict[str, dict]
    errors: list[dict]
    # Set by advance_node the moment any step fails. This is an
    # abort-on-first-failure policy: once set, route_node/pick_agent skip
    # straight to "done" (assemble) instead of dispatching remaining steps.
    aborted: bool
    answer: str | None
    status: Literal["running", "completed", "failed"]


def is_run_finished(state: RunState) -> bool:
    """True once the graph should stop dispatching steps and go to ``assemble``.

    True once a prior step's failure has set ``aborted`` (abort-on-first-failure
    — see ``advance_node`` in ``app/orchestrator/nodes.py``), or once
    ``current_step`` has advanced past the end of the step plan. Shared by
    ``route_node`` (``app/orchestrator/nodes.py``) and ``pick_agent``
    (``app/orchestrator/routing.py``) so the two checks can't drift out of
    sync with each other.
    """
    return bool(state.get("aborted")) or state["current_step"] >= len(state["steps"])
