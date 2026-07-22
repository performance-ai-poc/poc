"""Broader routing/orchestration coverage against the full /chat endpoint.

These go beyond the two required demo scenarios (test_api.py) to probe
edge cases in app/orchestrator/routing.py's keyword matching: rule vs.
word-appearance order, repeated keywords, casing, and substring
false-positives. Several of these assertions lock in *current, verified*
behavior rather than a claim about "correct" design — see each test's
docstring for what's confirmed vs. what's flagged as a design question.

Every test captures structured log output the same way test_api.py does,
and checks the same two invariants on every log line for that request:
the four correlation IDs are consistent, and no raw message content leaks
into the logs.
"""

from __future__ import annotations

import io
import json
import logging
import time

from fastapi.testclient import TestClient

from app.logging_utils import _JsonFormatter, get_logger
from app.main import app
from app.orchestrator.routing import build_step_plan

client = TestClient(app)


def _post_chat_capturing_logs(message: str) -> tuple[object, list[dict]]:
    """POST /chat while capturing every structured log line emitted for it.

    Returns (response, parsed_log_lines). Mirrors the StringIO-handler
    pattern already used in test_api.py rather than adding a new logging
    module or fixture.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    try:
        response = client.post("/chat", json={"message": message})
    finally:
        logger.removeHandler(handler)

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    return response, lines


def _assert_consistent_ids_and_no_leak(lines: list[dict], message: str) -> None:
    """Shared invariant check: one correlation-ID set per request, no raw content."""
    for key in ("run_id", "request_id", "session_id", "tenant_id"):
        values = {line[key] for line in lines}
        assert len(values) == 1, f"{key} was not consistent across all log lines: {values}"

    raw_log_text = json.dumps(lines)
    if message:
        assert message.lower() not in raw_log_text.lower(), "raw message content leaked into logs"


def _step_started_events(lines: list[dict]) -> list[dict]:
    return [line for line in lines if line["event"] == "agent.step_started"]


# ---------------------------------------------------------------------------
# 1. Single-rule match
# ---------------------------------------------------------------------------


def test_single_rule_match_produces_exactly_one_step():
    message = "Can you look up my orders?"
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200
    _assert_consistent_ids_and_no_leak(lines, message)

    steps = _step_started_events(lines)
    assert len(steps) == 1
    assert steps[0]["agent"] == "db_agent"


# ---------------------------------------------------------------------------
# 2. Two-rule match, api_agent words appear before db_agent words
# ---------------------------------------------------------------------------


def test_two_rule_match_follows_rule_definition_order_not_word_order():
    """DESIGN QUESTION, not a bug: build_step_plan() iterates ROUTING_RULES in
    its own fixed definition order and does existence checks only (`any(kw in
    message_lower ...)`), never checking *where* a keyword sits in the
    message. So step order is always rule-definition order (db_agent's rule
    is defined first, api_agent's second), regardless of which words appear
    first in the sentence.

    This test phrases the api_agent-triggering words BEFORE the
    db_agent-triggering words — the reverse of the previously-verified demo
    message — specifically to confirm the output order does NOT flip to
    match word-appearance order. If it did flip, that would indicate
    position-sensitive matching was accidentally introduced; it should not.

    Whether "rule order" or "word-appearance order" is the *intended*
    behavior is a real, unresolved design question — this test only confirms
    which one the current code implements.
    """
    message = "Please check the shipment tracking status, and also pull up my orders."
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200
    _assert_consistent_ids_and_no_leak(lines, message)

    steps = _step_started_events(lines)
    assert len(steps) == 2
    agents_in_order = [s["agent"] for s in steps]
    # db_agent first, api_agent second — matches ROUTING_RULES definition
    # order, NOT the word order in the message (api-words appeared first).
    assert agents_in_order == ["db_agent", "api_agent"]


# ---------------------------------------------------------------------------
# 3. All three rules match, in various word orders
# ---------------------------------------------------------------------------


ALL_THREE_RULES_VARIANTS = [
    (
        "rules_order",
        "I have a problem with my orders, please check the shipment status, "
        "and also see the escalation policy.",
    ),
    (
        "reverse_order",
        "Regarding our escalation policy, could you check the shipment tracking "
        "status, and also review my orders in the database?",
    ),
    (
        "interleaved_order",
        "My orders need review, but first check the escalation policy, then "
        "confirm shipment status.",
    ),
]


def test_all_three_rules_match_consistently_regardless_of_word_order():
    """Same design point as test 2, extended to three simultaneous matches.

    Three phrasings below trigger all three rules (orders/db, shipment-status/
    api, policy-escalation/db) with the trigger words in a different order in
    each sentence. If the step plan is genuinely rule-order-driven (as
    routing.py's code shows it is), all three variants must produce the exact
    same agent sequence: db_agent, api_agent, db_agent. A change in that
    sequence between variants would mean matching is accidentally
    position-sensitive somewhere — it is not expected to be, but this test
    exists specifically to catch it if it were.
    """
    results = {}
    for label, message in ALL_THREE_RULES_VARIANTS:
        response, lines = _post_chat_capturing_logs(message)
        assert response.status_code == 200
        _assert_consistent_ids_and_no_leak(lines, message)

        steps = _step_started_events(lines)
        assert len(steps) == 3
        results[label] = [s["agent"] for s in steps]

    # All three variants must agree on the exact same agent sequence.
    sequences = list(results.values())
    assert all(seq == sequences[0] for seq in sequences), results
    assert sequences[0] == ["db_agent", "api_agent", "db_agent"]


# ---------------------------------------------------------------------------
# 4. Empty message
# ---------------------------------------------------------------------------


def test_empty_message_rejected_by_schema_validation_before_routing():
    """schemas.py's ChatRequest.message has min_length=1, so an empty string
    is rejected by pydantic validation (422) before the request ever reaches
    generate_reply()/build_step_plan(). This confirms that existing
    behavior explicitly, rather than assuming the fallback path handles it:
    the fallback path is for a non-empty message that matches no keyword
    rule, not for a missing/empty message, which is a distinct, earlier
    validation failure.
    """
    response, lines = _post_chat_capturing_logs("")

    assert response.status_code == 422
    _assert_consistent_ids_and_no_leak(lines, "")

    # No orchestrator involvement at all for a request rejected at validation.
    assert _step_started_events(lines) == []
    events = [line["event"] for line in lines]
    assert "api.request.validation_error" in events


# ---------------------------------------------------------------------------
# 5. Very long message (at the max_length boundary) with scattered keywords
# ---------------------------------------------------------------------------


def _build_max_length_message(target_length: int = 10_000) -> str:
    prefix = (
        "I have concerns about my orders and need to check the shipment "
        "status, also please review the escalation policy. "
    )
    filler_unit = "filler text with no trigger words in it, just padding. "
    body = prefix
    while len(body) < target_length:
        body += filler_unit
    return body[:target_length]


def test_max_length_message_still_routes_correctly_and_performs_normally():
    message = _build_max_length_message(10_000)
    assert len(message) == 10_000

    start = time.perf_counter()
    response, lines = _post_chat_capturing_logs(message)
    elapsed_s = time.perf_counter() - start

    assert response.status_code == 200
    _assert_consistent_ids_and_no_leak(lines, message)

    steps = _step_started_events(lines)
    assert len(steps) == 3
    assert [s["agent"] for s in steps] == ["db_agent", "api_agent", "db_agent"]

    # Generous ceiling — this is local dict/string work, not I/O; should be
    # single-digit milliseconds in practice.
    assert elapsed_s < 1.0, f"request took {elapsed_s:.3f}s, unexpectedly slow"


# ---------------------------------------------------------------------------
# 6. Repeated keywords for the same rule
# ---------------------------------------------------------------------------


def test_repeated_keywords_produce_one_step_per_rule_not_per_occurrence():
    """build_step_plan() appends at most one step per ROUTING_RULES entry —
    the `if any(kw in message_lower for kw in keywords)` check is a single
    existence test per rule, not a per-keyword-occurrence loop. So repeating
    "order" three times must not produce three db_agent steps.
    """
    message = "I have an order, another order, and a third order to check, plus shipment status please."
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200
    _assert_consistent_ids_and_no_leak(lines, message)

    steps = _step_started_events(lines)
    # Exactly one step per matched rule (db_agent, api_agent) — not one per
    # keyword occurrence, and not one per keyword within a rule.
    assert len(steps) == 2
    assert [s["agent"] for s in steps] == ["db_agent", "api_agent"]


# ---------------------------------------------------------------------------
# 7. Case sensitivity
# ---------------------------------------------------------------------------


def test_mixed_case_keywords_still_match():
    message = "Please check my ORDERS and the Shipment Status."
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200
    _assert_consistent_ids_and_no_leak(lines, message)

    steps = _step_started_events(lines)
    assert len(steps) == 2
    assert [s["agent"] for s in steps] == ["db_agent", "api_agent"]


# ---------------------------------------------------------------------------
# 8. Substring false positive
# ---------------------------------------------------------------------------


def test_substring_match_inside_unrelated_word_is_no_longer_a_false_positive():
    """Regression test for the fixed bug: matching used to be plain substring
    search (`kw in message_lower`), so "disorder" matched "order" as a
    substring and this message — about tidying a closet, nothing to do with
    orders — used to produce a spurious db_agent step.

    routing.py now matches keywords via `\\b`-anchored regexes
    (_KEYWORD_PATTERNS), so "order" only matches as a whole word. This
    message matches no rule at all, so it takes the FALLBACK path — still
    one step, still db_agent (FALLBACK_AGENT happens to also be db_agent),
    but via the fallback branch rather than a genuine (false) rule match.
    The reply text distinguishes the two: a real rule-1 match always reads
    "...Look up matching records in the database.", while the fallback
    embeds the raw message itself.
    """
    message = "There's a lot of disorder in my closet, can you help me organize it?"
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200
    _assert_consistent_ids_and_no_leak(lines, message)

    steps = _step_started_events(lines)
    # Still one step (the fallback still needs *some* step — Section 6.1),
    # but it must not be the canned rule-1 instruction digest.
    assert len(steps) == 1
    assert steps[0]["agent"] == "db_agent"

    reply = response.json()["reply"]
    assert "Look up matching records in the database." not in reply
    assert "disorder" in reply.lower()  # fallback echoes the raw message


def test_tracking_no_longer_matches_inside_backtracking():
    """Same collision class as "order"/"disorder", found while auditing the
    other keywords: "backtracking" (e.g. an algorithm-design conversation)
    contains "tracking" as a substring and used to spuriously match
    api_agent's rule. Confirms no rule matches now — fallback path only.
    """
    message = "I've been debugging a backtracking algorithm for hours and I'm stuck."
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200
    _assert_consistent_ids_and_no_leak(lines, message)

    steps = _step_started_events(lines)
    assert len(steps) == 1
    reply = response.json()["reply"]
    assert "Check shipment status via the external API." not in reply
    assert "backtracking" in reply.lower()


def test_document_no_longer_matches_inside_documentation_or_undocumented():
    """Same collision class again: "documentation" and "undocumented" both
    contain "document" as a substring and used to spuriously match rule 3
    (policy/escalation/document -> db_agent), even though neither message
    has anything to do with policy search.
    """
    for message in (
        "Can you help me write documentation for this API?",
        "This behavior is undocumented and confusing.",
    ):
        response, lines = _post_chat_capturing_logs(message)

        assert response.status_code == 200
        _assert_consistent_ids_and_no_leak(lines, message)

        steps = _step_started_events(lines)
        assert len(steps) == 1
        reply = response.json()["reply"]
        assert "Search documents for the relevant policy." not in reply


# ---------------------------------------------------------------------------
# 9. Plural-only forms (regression: word-boundary matching requires each
#    form to be listed explicitly, unlike the old substring matching, which
#    matched plurals by coincidence via prefix overlap)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 10. Matched-rule instructions must preserve the user's actual request, not
#    just the canned per-rule sentence (fix for the audit finding: only the
#    no-match fallback used to keep the raw message).
# ---------------------------------------------------------------------------


def test_matched_rule_instruction_includes_both_canned_text_and_raw_message():
    """build_step_plan() is exercised directly (not via HTTP) because the
    raw instruction text is only ever logged as a digest (see
    app/orchestrator/nodes.py's _digest) — checking the returned step dict
    directly is the only way to confirm the specifics weren't dropped before
    they ever reach a (future) real agent.
    """
    message = "Get all orders placed by customer 42 in the last 7 days"
    steps = build_step_plan(message)

    assert len(steps) == 1
    instruction = steps[0]["instruction"]
    assert "Look up matching records in the database." in instruction
    assert message in instruction


def test_matched_rule_reply_contains_original_request_details_end_to_end():
    """End-to-end version of the test above: confirms the specifics survive
    all the way into the stub agent's summary/reply, while still never
    appearing in logs (metadata-only logging is about logs, not the reply
    payload — see test_valid_chat_message_does_not_leak_into_logs in
    test_api.py for that distinction).
    """
    message = "Get all orders placed by customer 42 in the last 7 days"
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "Look up matching records in the database." in reply
    assert message in reply

    raw_log_text = json.dumps(lines)
    assert message.lower() not in raw_log_text.lower()


# ---------------------------------------------------------------------------
# 11. Hyphenated compounds must not falsely match (fix for the audit finding:
#    plain \b treats "-" as a non-word character, so "re-order" and
#    "order-history" used to match the "order" keyword the same way a space
#    would separate it).
# ---------------------------------------------------------------------------


def test_hyphenated_compound_does_not_falsely_match_order_keyword():
    for message in (
        "There's a lot of re-order confusion in my closet, can you help me organize it?",
        "Let's review the order-history page design before we ship it.",
    ):
        response, lines = _post_chat_capturing_logs(message)

        assert response.status_code == 200
        _assert_consistent_ids_and_no_leak(lines, message)

        steps = _step_started_events(lines)
        # No rule genuinely matches either message — fallback path only.
        assert len(steps) == 1
        reply = response.json()["reply"]
        assert "Look up matching records in the database." not in reply


def test_standalone_order_keyword_still_matches_next_to_a_hyphen():
    """Confirms the tightened regex didn't overcorrect: a genuine standalone
    use of "order" immediately before/after a hyphen used as punctuation
    (not as part of a compound word) must still match.
    """
    message = "I have an order - can you check its status for me?"
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200
    _assert_consistent_ids_and_no_leak(lines, message)

    steps = _step_started_events(lines)
    agents = [s["agent"] for s in steps]
    assert "db_agent" in agents


def test_plural_only_shipment_form_still_matches_api_agent_rule():
    """"shipments" never appears in the message as "shipment" — only the
    plural form is present. ROUTING_RULES now lists "shipments" alongside
    "shipment" explicitly (routing.py), so \\b-anchored matching still finds
    it; before that addition, this would have fallen through to the
    fallback path instead of correctly routing to api_agent.
    """
    message = "Can you check my shipments? I placed several last week."
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200
    _assert_consistent_ids_and_no_leak(lines, message)

    steps = _step_started_events(lines)
    assert len(steps) == 1
    assert steps[0]["agent"] == "api_agent"

    reply = response.json()["reply"]
    assert "Check shipment status via the external API." in reply


# ---------------------------------------------------------------------------
# 12. Two matched rules that both map to the SAME agent (db_agent), with no
#    third agent involved — distinct from test 3 above (which always mixes
#    db_agent and api_agent). Confirms build_step_plan() keys steps by
#    per-rule sequence number, not by agent, so two db_agent steps never
#    collide in step_results and both are reflected in the assembled answer.
# ---------------------------------------------------------------------------


def test_two_rules_matching_the_same_agent_produce_two_independent_steps():
    message = "check my orders and our escalation policy please"
    response, lines = _post_chat_capturing_logs(message)

    assert response.status_code == 200
    _assert_consistent_ids_and_no_leak(lines, message)

    steps = _step_started_events(lines)
    # Both rule 1 ("order") and rule 3 ("policy") match; both route to
    # db_agent; neither api_agent rule matches.
    assert len(steps) == 2
    assert [s["agent"] for s in steps] == ["db_agent", "db_agent"]
    assert [s["step.sequence"] for s in steps] == [1, 2]

    # No key collision: both steps' distinct summaries show up in the
    # assembled answer, not just one overwriting the other.
    reply = response.json()["reply"]
    assert "Look up matching records in the database." in reply
    assert "Search documents for the relevant policy." in reply
