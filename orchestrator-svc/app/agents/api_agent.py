"""REST API Agent (Agent 2).

Interacts with HTTP services through the MCP server's http tools — for the
demo, a mock internal shipping-status service. This is the executor sub-agent
the whole telemetry story hangs on: it is one of the only two places an LLM is
called, and every tool call it makes flows through the shared retry helper, so
between them this node emits the full agent.* event set for its step
(architecture spec, sections 4.5 and 5.1):

    agent.llm_call        — planning the HTTP calls (this module)
    agent.tool_selected   — before each MCP call        (retry helper)
    agent.retried         — before each re-attempt       (retry helper)
    agent.tool_returned   — on each MCP response         (retry helper)

(agent.step_started / step_completed / step_failed for the step are emitted by
the graph's route/advance nodes, not here.)

Behavior loop (spec section 4.5):
  1. Fetch the API catalog (list_endpoints) — an allow-list of endpoints.
  2. LLM: instruction + catalog -> a structured call plan. The catalog is an
     allow-list, so the planner cannot invent URLs.
  3. Execute each call via http_get / http_post through the retry helper
     (GETs idempotent, POSTs not). 5xx/timeout retries happen inside the
     helper; exhaustion surfaces a terminal error.
  4. Normalize + summarize into the step result the orchestrator assembles.

Live vs offline (LLM + MCP transport) is governed by settings.agent_live_calls
and handled entirely in app/llm.py and app/mcp_client.py — both modes emit the
same telemetry, so this node's logic is transport-agnostic.

``call.sequence`` is a single running counter over every tool AND LLM call in
the step (1, 2, 3, ...), locating each event within the step (spec section 2).
"""

from __future__ import annotations

import time

from app.context import RequestContext
from app.llm import LLMError, plan_api_calls
from app.logging_utils import log_agent_llm_call
from app.mcp_client import call_tool
from app.retry import ToolError, call_tool_with_retry

GRAPH_NODE = "api_agent"

# TEST-ONLY, not demo/production behavior. Kept from the original stub so the
# orchestrator's abort-on-first-failure path stays exercisable without needing
# a live MCP failure: this trigger short-circuits the whole loop and returns a
# terminal error, exactly as an exhausted retry would. Checked against the raw
# message (not the canned instruction) so a multi-step plan can force failure
# on this agent's step specifically. The trigger string is not ordinary
# English, so demo traffic never hits it. The live failure/retry demo uses the
# MCP server's fail_next control tool instead (spec section 6, scenario 5).
FORCE_FAILURE_TRIGGER = "__FORCE_API_AGENT_FAILURE__"


def _tool_callable(ctx: RequestContext):
    """Bind the request context onto the MCP transport so correlation IDs ride
    along as headers, while presenting the (tool_name, args) signature the
    retry helper expects."""

    async def _call(tool_name: str, args: dict) -> dict:
        return await call_tool(tool_name, args, ctx=ctx)

    return _call


def _summarize(instruction: str, calls_made: list[dict]) -> str:
    """A short, deterministic natural-language summary for response assembly.

    The instruction is echoed verbatim so the assembled reply still reflects
    what the user actually asked (matching the rest of the pipeline); only
    call metadata — never response bodies — is added around it.
    """
    if calls_made:
        call_desc = ", ".join(
            f"{c['method']} {c['endpoint']} -> {c['status_code']}" for c in calls_made
        )
        prefix = f"Retrieved shipment status via {len(calls_made)} API call(s) ({call_desc})."
    else:
        prefix = "No API calls were required."
    return f"{prefix} {instruction}"


async def api_agent_node(state):
    step = state["steps"][state["current_step"]]
    ctx: RequestContext = state["ctx"]
    step_sequence = step["sequence"]
    instruction = step["instruction"]

    if FORCE_FAILURE_TRIGGER in state["message"]:
        state["step_results"][step["key"]] = {
            "status": "error",
            "summary": "",
            "duration_ms": 10,
            "error": "simulated_tool_timeout",
        }
        return state

    started = time.perf_counter()
    tool_callable = _tool_callable(ctx)
    call_sequence = 0
    calls_made: list[dict] = []
    collected: list[dict] = []

    def _duration_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        # 1. Fetch the API catalog (allow-list of endpoints).
        call_sequence += 1
        catalog_result = await call_tool_with_retry(
            ctx,
            graph_node=GRAPH_NODE,
            step_sequence=step_sequence,
            call_sequence=call_sequence,
            tool_name="list_endpoints",
            args={},
            tool_callable=tool_callable,
            idempotent=True,
        )
        api_catalog = catalog_result.get("endpoints", []) if isinstance(catalog_result, dict) else []

        # 2. Plan the HTTP calls (the sub-agent's single LLM call for this step).
        call_sequence += 1
        plan, llm_meta = await plan_api_calls(instruction, api_catalog)
        log_agent_llm_call(
            ctx,
            {
                "graph.node": GRAPH_NODE,
                "step.sequence": step_sequence,
                "call.sequence": call_sequence,
                **llm_meta,
            },
        )

        # 3. Execute each planned call through the retry helper. GETs are
        #    idempotent (retried on 5xx/timeout); POSTs are not (a timed-out
        #    write may already have applied — surface the error instead).
        for call in plan:
            call_sequence += 1
            method = call["method"]
            endpoint = call["endpoint"]
            if method == "POST":
                tool_name = "http_post"
                args = {"endpoint": endpoint, "body": call.get("body", {})}
                idempotent = False
            else:
                tool_name = "http_get"
                args = {"endpoint": endpoint, "params": call.get("params", {})}
                idempotent = True

            result = await call_tool_with_retry(
                ctx,
                graph_node=GRAPH_NODE,
                step_sequence=step_sequence,
                call_sequence=call_sequence,
                tool_name=tool_name,
                args=args,
                tool_callable=tool_callable,
                idempotent=idempotent,
            )
            calls_made.append(
                {"method": method, "endpoint": endpoint, "status_code": result.get("status_code")}
            )
            body = result.get("body")
            if isinstance(body, dict):
                collected.append(body)

    except (ToolError, LLMError) as exc:
        # Terminal, classified failure: a tool call exhausted its retries / hit
        # a non-retryable error (ToolError), or the LLM planning call failed —
        # e.g. the endpoint was unreachable (LLMError). Surface a metadata-only
        # category to the orchestrator, which marks the step failed and never
        # re-dispatches (single retry layer, spec 4.3). Never a crash: an
        # executor sub-agent degrades to agent.step_failed, per the fail-open
        # contract, so one flaky dependency can't 500 the whole /chat request.
        state["step_results"][step["key"]] = {
            "status": "error",
            "summary": "",
            "duration_ms": _duration_ms(),
            "error": exc.reason,
            "calls_made": calls_made,
            "data": {},
        }
        return state
    except Exception:  # noqa: BLE001 — defense-in-depth fail-open (see above).
        # Any unexpected error (a bug, an unforeseen dependency failure) still
        # becomes a terminal step error rather than an unhandled 500. The
        # generic category keeps this metadata-only; the specific reasons above
        # cover the failures we can name.
        state["step_results"][step["key"]] = {
            "status": "error",
            "summary": "",
            "duration_ms": _duration_ms(),
            "error": "agent_error",
            "calls_made": calls_made,
            "data": {},
        }
        return state

    state["step_results"][step["key"]] = {
        "status": "success",
        "summary": _summarize(instruction, calls_made),
        "duration_ms": _duration_ms(),
        "calls_made": calls_made,
        "data": {"results": collected},
    }
    return state
