"""Standalone dashboard traffic demo: one browser window, many tabs.

Opens N tabs (default 15) in a single Chromium window, each loading the customer
chat UI (the "client LLM"), then automatically sends one UNIQUE request per tab.
Every tab drives the real path (nginx -> orchestrator /chat -> agents -> MCP),
so the run produces a burst of correlated agent.* telemetry you can watch land
on the observability dashboard.

This file is self-contained and does not import or modify any project code.

Setup (once):
    pip install playwright
    playwright install chromium

Run:
    python open_tabs_demo.py                          # 15 tabs, http://customer.local
    python open_tabs_demo.py --tabs 12
    python open_tabs_demo.py --url http://localhost:8080   # if port-forwarding customer-ui
    python open_tabs_demo.py --headless               # no visible windows

Notes:
  * The client UI must be reachable at --url. With the Helm stack + `minikube
    tunnel` running, that's http://customer.local. Otherwise port-forward the
    customer-ui service to :8080 and pass --url http://localhost:8080.
  * The LLM endpoint is slow and there's a single orchestrator replica, so the
    sends are staggered (--stagger) to spread the load rather than firing all 15
    at once. Increase --stagger or --timeout if requests time out under load.
"""

from __future__ import annotations

import argparse
import asyncio

from playwright.async_api import Page, async_playwright

# Selectors from customer-ui/src/App.tsx. The UI submits on the Send button (a
# bare Enter in the textarea just inserts a newline), so we click Send.
INPUT_SELECTOR = "textarea.chat-input"
SEND_SELECTOR = "button.chat-send"
MESSAGE_SELECTOR = ".chat-thread .chat-message"
TYPING_SELECTOR = ".chat-typing"
ERROR_SELECTOR = ".chat-error"

# 15 distinct prompts spanning the DB path, the REST API path, and document
# search, so the tabs exercise different agents rather than all taking one route.
UNIQUE_QUERIES = [
    "Can you look up my orders?",
    "Show me the status of order 1001.",
    "What carrier is handling order 1002?",
    "Is order 1003 delayed, and with which carrier?",
    "Look up recent orders for customer 42.",
    "What is the schema for the orders data?",
    "Find documents about shipment escalation.",
    "Search for notes on delivery delays.",
    "Summarize the latest order issues.",
    "Which orders failed recently?",
    "Check the shipment status for order 1001 with the carrier.",
    "What does our escalation policy say about failed orders?",
    "List the most recent shipments and their carriers.",
    "Find all failed orders and tell me their current shipment status.",
    "What documents do we have about returns and refunds?",
]


def _log(tab: int, message: str) -> None:
    print(f"[tab-{tab:02d}] {message}", flush=True)


async def _send_query(page: Page, tab: int, query: str, timeout_ms: int) -> None:
    """Type one query, submit it, and wait for the reply (or an error) to render."""
    await page.wait_for_selector(INPUT_SELECTOR, timeout=timeout_ms)
    await page.fill(INPUT_SELECTOR, query)
    _log(tab, f"sending: {query}")

    # Capture the /chat response so we can print run_id — the ID that ties this
    # request to the server-side telemetry. The UI doesn't display it.
    run_id: dict[str, str] = {}

    async def _on_response(response) -> None:
        if response.url.rstrip("/").endswith("/chat") and response.request.method == "POST":
            try:
                body = await response.json()
                run_id["value"] = body.get("run_id", "")
            except Exception:  # noqa: BLE001 — telemetry nicety, never fail the run over it.
                pass

    before_count = await page.locator(MESSAGE_SELECTOR).count()
    page.on("response", _on_response)
    try:
        await page.click(SEND_SELECTOR)
        # Done when the thread grew by two nodes (our message + the reply/error)
        # AND the typing indicator is gone (it briefly satisfies the count alone).
        await page.wait_for_function(
            """([sel, typingSel, before]) => {
                const total = document.querySelectorAll(sel).length;
                const typing = document.querySelector(typingSel);
                return total > before + 1 && !typing;
            }""",
            arg=[MESSAGE_SELECTOR, TYPING_SELECTOR, before_count],
            timeout=timeout_ms,
        )
    finally:
        page.remove_listener("response", _on_response)

    result = await page.evaluate(
        """([messageSel, errorSel]) => {
            const nodes = document.querySelectorAll(messageSel);
            const last = nodes[nodes.length - 1];
            if (!last) return { error: null, reply: null };
            const err = last.querySelector(errorSel);
            if (err) return { error: err.innerText, reply: null };
            const p = last.querySelector("p");
            return { error: null, reply: p ? p.innerText : "" };
        }""",
        [MESSAGE_SELECTOR, ERROR_SELECTOR],
    )

    rid = run_id.get("value", "")
    if result["error"] is not None:
        _log(tab, f"ERROR{f' (run_id={rid})' if rid else ''}: {result['error'].strip()}")
        return
    reply = (result["reply"] or "").strip()
    _log(tab, f"reply{f' (run_id={rid})' if rid else ''}: {reply}")


async def _run_tab(
    context, tab: int, url: str, query: str, timeout_ms: int,
    start_delay_s: float,
) -> None:
    # Stagger the whole tab (open + navigate + send). Opening many headed tabs at
    # the exact same instant can crash the browser process, so spread them out —
    # and this also keeps 15 slow LLM calls from all firing at once.
    if start_delay_s:
        await asyncio.sleep(start_delay_s)
    page: Page | None = None
    try:
        page = await context.new_page()  # a new TAB in the shared window
        await page.goto(url, wait_until="domcontentloaded")
        await _send_query(page, tab, query, timeout_ms)
    except Exception as exc:  # noqa: BLE001 — one tab failing shouldn't sink the rest.
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        _log(tab, f"failed: {type(exc).__name__}: {detail}")


async def _main(args: argparse.Namespace) -> None:
    queries = (UNIQUE_QUERIES * ((args.tabs // len(UNIQUE_QUERIES)) + 1))[: args.tabs]
    print(f"opening {args.tabs} tab(s) against {args.url}", flush=True)

    async with async_playwright() as pw:
        # Stability flags for headed Chromium on Windows — opening many tabs
        # without these can crash the browser process ("Connection closed while
        # reading from the driver").
        browser = await pw.chromium.launch(
            headless=args.headless,
            args=[
                "--window-size=1400,900",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
            ],
        )
        # One context = one window; each _run_tab opens a new TAB in it (lazily,
        # staggered) so a single failure is isolated and logged, not fatal.
        context = await browser.new_context()
        try:
            await asyncio.gather(
                *(
                    _run_tab(
                        context=context,
                        tab=i + 1,
                        url=args.url,
                        query=queries[i],
                        timeout_ms=args.timeout,
                        start_delay_s=i * args.stagger,
                    )
                    for i in range(args.tabs)
                )
            )
            print("all tabs done", flush=True)
            if args.hold:
                print(f"holding windows open {args.hold}s...", flush=True)
                await asyncio.sleep(args.hold)
        finally:
            try:
                await browser.close()
            except Exception as exc:  # noqa: BLE001 — never let cleanup mask real output.
                print(f"(browser close warning: {type(exc).__name__})", flush=True)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Open N tabs of the chat UI and send a unique request in each.")
    p.add_argument("--url", default="http://customer.local", help="Chat UI base URL (default: %(default)s).")
    p.add_argument("--tabs", type=int, default=15, help="Number of tabs / unique requests (default: %(default)s).")
    p.add_argument("--stagger", type=float, default=1.5, help="Seconds between each tab's send, to spread LLM load (default: %(default)s).")
    p.add_argument("--timeout", type=int, default=120000, help="Per-step timeout in ms (default: %(default)s).")
    p.add_argument("--hold", type=float, default=10.0, help="Seconds to keep the window open after all replies (default: %(default)s).")
    p.add_argument("--headless", action="store_true", help="Run without visible windows (default: headed).")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(_main(_parse_args()))
