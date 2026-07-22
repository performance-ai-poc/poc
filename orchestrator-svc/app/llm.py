"""LLM planner for the REST API Agent.

The sub-agents are the only components in the system that call an LLM (the
orchestrator is deterministic — spec section 3.2). This module wraps the one
LLM call the REST API Agent makes: turning an instruction plus the injected
API catalog into a structured call plan — ``[{method, endpoint, params|body}]``
— that the agent then executes through the MCP http tools.

``plan_api_calls`` returns ``(plan, meta)`` where ``meta`` carries exactly the
fields ``agent.llm_call`` needs (model_id, input_tokens, output_tokens,
latency_ms), so the agent can emit that event uniformly regardless of mode:

  - Live (settings.agent_live_calls True): a real chat-completions call to the
    OpenAI-compatible endpoint; tokens come from the response's usage block.
  - Offline (default): a deterministic plan built from the catalog, with token
    counts estimated from text length so the telemetry event is still present
    and plausible. No network — this is what tests / un-provisioned envs use.

The catalog is an allow-list: the planner may only target endpoints it lists,
so a live model cannot invent URLs (spec section 4.5).
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.config import settings


class LLMError(Exception):
    """Raised when the LLM planning call fails (unreachable, timeout, bad HTTP
    status, or an unparseable response). Carries a short, metadata-only
    ``reason`` category — never a raw exception message or the prompt — so the
    REST API Agent can surface it into a terminal step error (which becomes
    ``agent.step_failed``) instead of letting a raw exception crash the request.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"llm planning failed: {reason}")
        self.reason = reason


# One system prompt, shared by both modes' intent. In offline mode it is not
# sent anywhere, but keeping it here documents exactly what the live model is
# asked to do.
_SYSTEM_PROMPT = (
    "You are the REST API Agent's planner. Given an instruction and a JSON API "
    "catalog (an allow-list of endpoints, each with a name, method, and params), "
    "return ONLY a JSON object of the form "
    '{"calls": [{"method": "GET|POST", "endpoint": "<catalog endpoint name>", '
    '"params": {...}, "body": {...}}]}. The "endpoint" MUST be one of the catalog '
    "entries' \"name\" values (NOT a URL path). Fill params/body from each "
    "endpoint's declared params. Use GET for reads and POST for writes. Return no "
    "prose, only the JSON object."
)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for offline-mode telemetry."""
    return max(1, len(text) // 4)


def _catalog_names(api_catalog: list[dict]) -> list[str]:
    return [str(ep.get("name")) for ep in api_catalog if ep.get("name")]


def _deterministic_plan(instruction: str, api_catalog: list[dict]) -> list[dict]:
    """A safe default plan: one GET against the first read endpoint in the
    catalog, addressed by its ``name``. Used as the whole plan offline, and as
    the fallback when a live model returns something unparseable."""
    for endpoint in api_catalog:
        if str(endpoint.get("method", "GET")).upper() == "GET":
            return [{"method": "GET", "endpoint": endpoint["name"], "params": {}}]
    # No GET endpoint advertised — fall back to the first endpoint of any kind.
    if api_catalog:
        first = api_catalog[0]
        return [{"method": str(first.get("method", "GET")).upper(), "endpoint": first["name"], "params": {}}]
    return []


def _sanitize_plan(plan: Any, api_catalog: list[dict]) -> list[dict]:
    """Keep only well-formed calls whose endpoint name is in the allow-list."""
    allowed = set(_catalog_names(api_catalog))
    clean: list[dict] = []
    if not isinstance(plan, list):
        return clean
    for call in plan:
        if not isinstance(call, dict):
            continue
        endpoint = call.get("endpoint")
        method = str(call.get("method", "GET")).upper()
        if endpoint not in allowed or method not in ("GET", "POST"):
            continue
        entry = {"method": method, "endpoint": endpoint}
        if isinstance(call.get("params"), dict):
            entry["params"] = call["params"]
        if isinstance(call.get("body"), dict):
            entry["body"] = call["body"]
        clean.append(entry)
    return clean


async def plan_api_calls(instruction: str, api_catalog: list[dict]) -> tuple[list[dict], dict]:
    """Return ``(plan, meta)`` — the call plan and the agent.llm_call metadata."""
    if settings.agent_live_calls:
        return await _plan_live(instruction, api_catalog)
    return _plan_offline(instruction, api_catalog)


# ---------------------------------------------------------------------------
# Offline planner
# ---------------------------------------------------------------------------


def _plan_offline(instruction: str, api_catalog: list[dict]) -> tuple[list[dict], dict]:
    plan = _deterministic_plan(instruction, api_catalog)
    catalog_text = json.dumps(api_catalog, default=str)
    plan_text = json.dumps(plan, default=str)
    meta = {
        "model_id": settings.llm_model,
        "input_tokens": _estimate_tokens(_SYSTEM_PROMPT + instruction + catalog_text),
        "output_tokens": _estimate_tokens(plan_text),
        "latency_ms": 0,
    }
    return plan, meta


# ---------------------------------------------------------------------------
# Live planner
# ---------------------------------------------------------------------------


async def _plan_live(instruction: str, api_catalog: list[dict]) -> tuple[list[dict], dict]:
    import httpx

    user_content = (
        f"Instruction:\n{instruction}\n\n"
        f"API catalog (allow-list):\n{json.dumps(api_catalog)}"
    )
    request_body = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as http:
            response = await http.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                json=request_body,
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"llm_http_{exc.response.status_code}") from exc
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # Endpoint unreachable — e.g. not on the required network/VPN, DNS,
        # refused connection. Surfaced as a terminal step error, not a crash.
        raise LLMError("llm_unreachable") from None
    except httpx.TimeoutException:
        raise LLMError("llm_timeout") from None
    except httpx.RequestError:
        raise LLMError("llm_unreachable") from None
    latency_ms = int((time.perf_counter() - started) * 1000)

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("llm_bad_response") from exc
    plan = _sanitize_plan(_extract_calls(content), api_catalog)
    if not plan:
        # Model returned nothing usable — fall back so the step still runs, but
        # keep the real token/latency numbers from the call that just happened.
        plan = _deterministic_plan(instruction, api_catalog)

    usage = payload.get("usage") or {}
    meta = {
        "model_id": payload.get("model", settings.llm_model),
        "input_tokens": int(usage.get("prompt_tokens", 0)),
        "output_tokens": int(usage.get("completion_tokens", 0)),
        "latency_ms": latency_ms,
    }
    return plan, meta


def _extract_calls(content: str) -> Any:
    """Pull the ``calls`` array out of the model's response.

    Tolerates the model wrapping JSON in prose or ```json fences by scanning
    for the first balanced JSON object; returns [] if nothing parses.
    """
    content = content.strip()
    # Strip a ```json ... ``` fence if present.
    if content.startswith("```"):
        content = content.strip("`")
        if content.lstrip().lower().startswith("json"):
            content = content.lstrip()[4:]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            parsed = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return []
    if isinstance(parsed, dict):
        return parsed.get("calls", [])
    return parsed
