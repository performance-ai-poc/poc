# Demo scripts

| Script | Purpose |
| --- | --- |
| `browser_demo.py` | Drives the chat UI in parallel browsers to generate correlated telemetry. |
| `verify_teardown.sh` | Proves the app survives removal of the observability plane. |

---

## Browser demo

Opens several browsers in parallel, each loading the customer chat UI, typing a
query and submitting it. Every run drives the real path (nginx -> orchestrator
`/chat` -> agents -> MCP server), so it exercises the whole stack and produces
the correlated telemetry the dashboard consumes.

Playwright is used rather than Selenium: it drives multiple browsers
concurrently from one async process and waits on elements automatically, so the
script stays small and doesn't need explicit sleeps.

### Setup

```bash
pip install -r demo/requirements.txt
playwright install chromium
```

### Bring the app up

The UI is served by nginx (the `customer-ui` image), which proxies `/chat` to
the orchestrator. Any URL where the chat page loads will work. With the Helm
stack running, the documented port-forward exposes it on `:8080`:

```bash
make port-forward-customer-ui
```

#### Or run it entirely locally, no cluster

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

### Run

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

### Notes

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

---

## Teardown verification

The teardown demo removes the observability plane and shows the multi-agent
application carrying on regardless. `verify_teardown.sh` is the evidence for
that claim — run it *after* the uninstall:

```bash
make uninstall-observability     # or obs-ctl option 2
make verify-teardown
```

It is read-only. It inspects the cluster and sends one chat request; it never
installs, uninstalls or deletes anything, so it is safe to re-run.

### What it checks

| # | Check |
| --- | --- |
| 1 | The `observability` Helm release is no longer installed. |
| 2 | No observability pods remain, and no orphaned cluster-scoped RBAC was left behind. |
| 3 | The `demo` release is still installed and every long-running application pod is Ready. |
| 4 | `POST /chat` on the orchestrator still returns 200 with a reply. |

Check 2 looks at the cluster as well as the release list because a botched
uninstall can drop the Helm release record while leaving the Collector's
ClusterRole and ClusterRoleBinding behind — those are cluster-scoped, so they
outlive the namespace's objects.

It also reports the OpenObserve PVC, which **survives the uninstall by design**:
`openObserve.persistence.keepOnDelete` stamps `helm.sh/resource-policy: keep`,
so captured telemetry is still there after a reinstall. It keeps the
observability labels, so a `kubectl get pvc` during the demo will show it — the
script calls it out as retained rather than staying silent and leaving the
"purged" claim looking wrong. A leftover PVC *without* that annotation is a real
orphan and does fail the check.

Check 3 tests pod *readiness*, not just phase: a pod can sit in `Running` with a
container crash-looping behind it. Completed pods are skipped and reported
separately — `mcp-seed` is a `post-install` Helm hook with
`hook-delete-policy: before-hook-creation`, so its `Completed` pod legitimately
outlives the install and must not read as a failure.

### Ports

Check 4 always starts its own port-forward, on `:18001`, and tears it down on
exit. It deliberately does not reuse whatever is already listening: the point of
the check is that the *cluster's* orchestrator survived, and an orchestrator run
locally on `:8001` (see above) would answer just as happily and turn a dead
deployment into a false pass. The unusual port also keeps it clear of a
`make port-forward-orchestrator` you already have running.

### Configuration

Every value is an environment variable, so the make target is a thin wrapper.
Override any of them to point at a non-standard deploy:

| Variable | Default | |
| --- | --- | --- |
| `NAMESPACE` | `default` | matches `make/common.mk` |
| `RELEASE` | `demo` | matches `make/common.mk` |
| `OBSERVABILITY_RELEASE` | `observability` | matches `make/common.mk` |
| `ORCHESTRATOR_LOCAL_PORT` | `18001` | dedicated; *not* the 8001 in `make/port-forward.mk` |

```bash
ORCHESTRATOR_LOCAL_PORT=8999 ./demo/verify_teardown.sh
```

Exit code is 0 when every check passes, 1 otherwise, so it can gate a scripted
demo run.

### Selectors

Both charts stamp `app.kubernetes.io/instance=<release>` on every pod template,
so the release name alone selects everything a chart owns. There is no `tier` or
`app` label on these workloads — selectors like `-l tier=agent` match nothing.
