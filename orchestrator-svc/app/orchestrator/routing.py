"""Deterministic keyword-based routing for the orchestrator graph.

See docs/orchestration-routing-implementation-doc.md, Section 6. Routing is
fully deterministic — no LLM call is involved anywhere in deciding which
sub-agent(s) handle a message; this is a confirmed architectural decision
(Section 2), not a placeholder for a future model call.

ROUTING_RULES is a placeholder tuned to the example scenario circulated so
far — it must be retuned to the team's actual rehearsed demo message once
finalized (Section 6.1).
"""

from __future__ import annotations

import re

from .state import is_run_finished

ROUTING_RULES = [
    # "failed order" was deliberately removed from this rule's keyword list:
    # it is fully subsumed by the standalone "order" keyword below (any
    # message containing "failed order" already contains "order") and could
    # never fire independently — it was dead weight, not a distinct signal.
    (["order", "orders", "database", "databases"], "db_agent",
     "Look up matching records in the database."),
    (["shipment", "shipments", "status", "carrier", "carriers", "tracking"], "api_agent",
     "Check shipment status via the external API."),
    (["policy", "escalation", "escalations", "document", "documents"], "db_agent",
     "Search documents for the relevant policy."),
]
FALLBACK_AGENT = "db_agent"

# Word-boundary-aware matching, not substring search: a bare `in` check let
# short/common keywords match inside unrelated words — "order" matched
# "disorder"/"reorder"/"border"/"orderly", "tracking" matched
# "backtracking", and "document" matched "documentation"/"undocumented" —
# producing confident, wrong routing on messages that never meant any of
# that.
#
# Plain `\b` isn't quite enough on its own, though: `\b` treats "-" as a
# non-word character, so it still (wrongly) matches "order" inside
# hyphenated compounds like "re-order" or "order-history" — a hyphen creates
# a word boundary same as a space does. The lookaround pair below tightens
# this: (?<![\w-]) requires the character immediately before the keyword to
# be neither a word character NOR a hyphen (or the start of the string), and
# (?![\w-]) requires the same immediately after (or end of string). That
# excludes both underscore/alnum-adjacency (plain \b already handled that)
# and hyphen-adjacency (\b alone did not), while still matching a keyword
# surrounded by spaces, punctuation other than "-", or string boundaries.
_KEYWORD_PATTERNS = {
    keyword: re.compile(rf"(?<![\w-]){re.escape(keyword)}(?![\w-])")
    for keywords, _agent, _instruction in ROUTING_RULES
    for keyword in keywords
}


def _keyword_matches(keyword: str, message_lower: str) -> bool:
    return _KEYWORD_PATTERNS[keyword].search(message_lower) is not None


def build_step_plan(message: str) -> list[dict]:
    """Builds an ORDERED list of steps from keyword matches in the message.

    A single message can produce multiple sequential steps. If no keyword
    rule matches, the fallback step below is used instead of returning an
    empty plan — an empty plan would be a silent failure with nothing to
    route to (Section 6.1: fallback behavior is required, not optional).

    A matched rule's instruction combines the canned per-rule sentence with
    the raw message rather than replacing it. The canned sentence alone
    discards every specific of what the user actually asked (which orders,
    what date range, which carrier) — fine for a stub that never reads
    ``instruction``, but a real DB/API agent needs the specifics to generate
    a correct SQL query or API call, not just the category of request. Only
    the no-match fallback previously preserved the raw message; every
    matched-rule step now does too.
    """
    message_lower = message.lower()
    steps = []
    sequence = 1
    for keywords, agent, instruction in ROUTING_RULES:
        if any(_keyword_matches(kw, message_lower) for kw in keywords):
            steps.append({
                "key": f"step-{sequence}",
                "agent": agent,
                "instruction": f"{instruction} Original request: {message}",
                "sequence": sequence,
                "status": "pending",
            })
            sequence += 1

    if not steps:
        steps.append({
            "key": "step-1",
            "agent": FALLBACK_AGENT,
            "instruction": message,
            "sequence": 1,
            "status": "pending",
        })
    return steps


def pick_agent(state) -> str:
    """Conditional-edge function. Returns 'db', 'api', or 'done'.

    ``aborted`` (set by ``advance_node`` the moment any step fails — see
    ``app/orchestrator/nodes.py``) short-circuits straight to "done" even if
    pending steps remain: this graph implements abort-on-first-failure, not
    "keep going regardless." is_run_finished() (app.orchestrator.state) is
    the single shared source of truth for this guard, so it can't drift out
    of sync with route_node's identical check in
    app/orchestrator/nodes.py.
    """
    if is_run_finished(state):
        return "done"
    agent = state["steps"][state["current_step"]]["agent"]
    if agent == "db_agent":
        return "db"
    elif agent == "api_agent":
        return "api"
    else:
        # A typo in ROUTING_RULES or a new agent type added without updating
        # this function must fail loudly, not silently route to api_agent.
        raise ValueError(f"pick_agent: unrecognized agent value {agent!r} in step plan")
