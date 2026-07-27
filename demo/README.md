# Browser demo

Opens several browsers in parallel, each loading the customer chat UI, typing a
query and submitting it. Every run drives the real path (nginx -> orchestrator
`/chat` -> agents -> MCP server), so it exercises the whole stack and produces
the correlated telemetry the dashboard consumes.

Playwright is used rather than Selenium: it drives multiple browsers
concurrently from one async process and waits on elements automatically, so the
script stays small and doesn't need explicit sleeps.

## Setup

```bash
pip install -r demo/requirements.txt
playwright install chromium
```

## Bring the app up

The UI is served by nginx (the `customer-ui` image), which proxies `/chat` to
the orchestrator. Any URL where the chat page loads will work. With the Helm
stack running, the documented port-forward exposes it on `:8080`:

```bash
make port-forward-customer-ui
```

### Or run it entirely locally, no cluster

The orchestrator's offline mode is deterministic and self-contained, so the demo
runs with no Postgres, no MCP server and no LLM:

```bash
# 1. orchestrator (offline mode is the default)
cd orchestrator-svc && python -m uvicorn app.main:app --port 8001

# 2. build the UI and serve it with /chat proxied to the orchestrator,
#    which is what nginx does in the deployed stack
cd customer-ui && npm run build
```

Serve `customer-ui/dist` on `:8080` with any static server that forwards
`POST /chat` to `http://127.0.0.1:8001/chat`, then point `--url` at it.

## Run

```bash
python demo/browser_demo.py                                  # 3 headed browsers
python demo/browser_demo.py --browsers 4 --url http://localhost:8080
python demo/browser_demo.py --repeat 3                       # 3 queries each
python demo/browser_demo.py --headless                       # CI / no display
python demo/browser_demo.py --query "Show failed orders from last week."
```

Windows are tiled in a 2x2 grid so up to four are visible side by side.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--url` | `http://localhost:8080` | Chat UI base URL |
| `--browsers` | `3` | Parallel browsers |
| `--repeat` | `1` | Queries each browser sends |
| `--headless` | off | Run without visible windows |
| `--timeout` | `60000` | Per-step timeout (ms) |
| `--delay` | `0` | Seconds between turns, to pace a live demo |
| `--hold` | `0` | Seconds to keep windows open at the end |
| `--query` | demo set | Query to send; repeatable, assigned round-robin |

Offline replies come back in milliseconds, so for a watchable demo pace it:

```bash
python demo/browser_demo.py --repeat 3 --delay 2 --hold 5
```

Output, one line per event:

```
[browser-0] launching 3 browser(s) against http://localhost:8080
[browser-1] sending: Check the shipment status for order 1001 with the carrier.
[browser-1] reply (run_id=6f2a...): Here is the shipment status for order 1001...
```

`run_id` is read from the `/chat` response, so a reply here can be matched
against the orchestrator and MCP server logs for the same run.

## Notes

- The default queries cover the DB, REST and document-search paths so parallel
  browsers exercise different agents rather than all taking the same route.
- The UI submits on the Send button or Ctrl/Cmd+Enter; a bare Enter in the
  textarea just inserts a newline. The script clicks Send.
- Selectors (`textarea.chat-input`, `button.chat-send`, `.chat-message`,
  `.chat-typing`, `.chat-error`) come from `customer-ui/src/App.tsx`. The chat
  is a scrolling thread, so the script waits for the message count to grow
  *and* the typing indicator to disappear, then reads whichever message is
  last in `.chat-thread` — the Send button itself doesn't reliably signal
  "done" since the composer clears on submit and re-disables the button for
  an unrelated reason (empty input). If that markup changes, update the
  selectors at the top of `browser_demo.py`.
- One browser failing is logged and does not stop the others.
