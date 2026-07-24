"""Browser-driven demo workload generator.

Opens N browsers in parallel, each loading the customer chat UI, typing a query,
and submitting it, then waits for the assistant's reply. This drives the real
front end (nginx -> orchestrator /chat -> agents -> MCP), so one run exercises
the whole stack and produces correlated telemetry the dashboard can show.

Usage:
    pip install -r demo/requirements.txt
    playwright install chromium
    python demo/browser_demo.py                    # 3 headed browsers, default queries
    python demo/browser_demo.py --browsers 4 --url http://localhost:8080
    python demo/browser_demo.py --headless --repeat 3

The UI is served by nginx (customer-ui), which proxies /chat to the orchestrator.
Bring the stack up first (e.g. `make port-forward-customer-ui`, which exposes it
on :8080) and point --url at it.
"""

from __future__ import annotations

import argparse
import asyncio

from playwright.async_api import Page, async_playwright

# Selectors from customer-ui/src/App.tsx. Note the UI submits on the Send button
# or Ctrl/Cmd+Enter — a bare Enter in the textarea inserts a newline — so we click
# Send rather than press Enter.
INPUT_SELECTOR = "textarea.chat-input"
SEND_SELECTOR = "button.chat-send"
REPLY_SELECTOR = ".chat-reply p"
ERROR_SELECTOR = ".chat-error"

# The demo scenario query plus two narrower ones, so parallel browsers send a mix
# of DB, API, and document-search work rather than all hitting the same path.
DEFAULT_QUERIES = [
    "Find all failed orders from last week, check their current shipment status "
    "with the carrier, and tell me what our escalation policy says we should do.",
    "Check the shipment status for order 1001 with the carrier.",
    "What does our escalation policy say about failed orders?",
]

# A 2x2 grid so up to four headed windows are visible side by side for the demo.
WINDOW_W, WINDOW_H = 720, 640
GRID = [(0, 0), (WINDOW_W, 0), (0, WINDOW_H), (WINDOW_W, WINDOW_H)]


def _log(worker: int, message: str) -> None:
    print(f"[browser-{worker}] {message}", flush=True)


async def _send_query(page: Page, worker: int, query: str, timeout_ms: int) -> None:
    """Type one query, submit it, and wait for the reply (or an error) to render."""
    await page.fill(INPUT_SELECTOR, query)
    _log(worker, f"sending: {query[:70]}{'...' if len(query) > 70 else ''}")

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

    page.on("response", _on_response)
    try:
        await page.click(SEND_SELECTOR)
        # The Send button re-enables (loading -> false) once the turn completes.
        await page.wait_for_selector(f"{SEND_SELECTOR}:not([disabled])", timeout=timeout_ms)
    finally:
        page.remove_listener("response", _on_response)

    error = await page.query_selector(ERROR_SELECTOR)
    if error is not None:
        _log(worker, f"ERROR: {(await error.inner_text()).strip()}")
        return

    reply = await page.text_content(REPLY_SELECTOR)
    rid = run_id.get("value", "")
    _log(worker, f"reply{f' (run_id={rid})' if rid else ''}: {(reply or '').strip()}")


async def _run_worker(
    worker: int, url: str, query: str, headless: bool,
    repeat: int, timeout_ms: int, delay_s: float, hold_s: float,
) -> None:
    async with async_playwright() as pw:
        x, y = GRID[worker % len(GRID)]
        browser = await pw.chromium.launch(
            headless=headless,
            args=[f"--window-position={x},{y}", f"--window-size={WINDOW_W},{WINDOW_H}"],
        )
        page = await browser.new_page(viewport={"width": WINDOW_W, "height": WINDOW_H})
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_selector(INPUT_SELECTOR, timeout=timeout_ms)
            for turn in range(repeat):
                if turn and delay_s:
                    await asyncio.sleep(delay_s)
                await _send_query(page, worker, query, timeout_ms)
            if hold_s:
                await asyncio.sleep(hold_s)
        except Exception as exc:  # noqa: BLE001 — one browser failing shouldn't sink the rest.
            # Playwright errors are multi-line; keep the log one line per event.
            detail = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
            _log(worker, f"failed: {type(exc).__name__}: {detail}")
        finally:
            await browser.close()


async def _main(args: argparse.Namespace) -> None:
    queries = args.query or DEFAULT_QUERIES
    _log(0, f"launching {args.browsers} browser(s) against {args.url}")
    await asyncio.gather(
        *(
            _run_worker(
                worker=i,
                url=args.url,
                query=queries[i % len(queries)],
                headless=args.headless,
                repeat=args.repeat,
                timeout_ms=args.timeout,
                delay_s=args.delay,
                hold_s=args.hold,
            )
            for i in range(args.browsers)
        )
    )
    _log(0, "done")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open N browsers and drive the chat UI in parallel.")
    parser.add_argument("--url", default="http://localhost:8080", help="Chat UI base URL (default: %(default)s).")
    parser.add_argument("--browsers", type=int, default=3, help="Number of parallel browsers (default: %(default)s).")
    parser.add_argument("--repeat", type=int, default=1, help="Queries each browser sends (default: %(default)s).")
    parser.add_argument("--headless", action="store_true", help="Run headless (default: headed, so it's visible).")
    parser.add_argument("--timeout", type=int, default=60000, help="Per-step timeout in ms (default: %(default)s).")
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds to pause between turns (default: %(default)s).")
    parser.add_argument("--hold", type=float, default=0.0, help="Seconds to keep windows open at the end (default: %(default)s).")
    parser.add_argument(
        "--query",
        action="append",
        help="A query to send; repeatable. Assigned round-robin to browsers. Defaults to the demo set.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_main(_parse_args()))
