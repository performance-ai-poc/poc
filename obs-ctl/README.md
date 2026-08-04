# obs-ctl

Terminal UI for installing and removing the Observability Plane independently of
the AI Chat application. It is the Step 2 tool in the Multi-Agent Observability
Guide: the demo installs observability, shows the dashboard, then removes it and
demonstrates that the multi-agent core keeps serving.

Written in Go with [Bubble Tea](https://github.com/charmbracelet/bubbletea) and
Lip Gloss. It is a thin, deliberate wrapper around Helm — every action is a real
`helm` invocation, not a simulation.

## Run

The Helm chart path is relative (`../infra/helm/observability`), so it **must be
run from this directory**:

```bash
cd obs-ctl
go run .
```

Or build a binary:

```bash
cd obs-ctl && go build -o obs-ctl .
./obs-ctl
```

## Keys

| Key | Action |
| --- | --- |
| `1` | Install the Observability Plane — prompts for a license key first |
| `2` | Remove the Observability Plane |
| `enter` | Submit the license key |
| `o` | Launch the dashboard (only after a successful install) |
| `q` / `ctrl+c` | Exit |

The license key for the demo is `0000`.

## What it actually runs

| Action | Command |
| --- | --- |
| Install | `helm upgrade --install observability ../infra/helm/observability -n default --create-namespace --wait --timeout 5m` |
| Remove | `helm uninstall observability -n default` |
| Dashboard | `kubectl port-forward -n default service/observability-dashboard-ui 8082:80` |

Before either action it checks that `helm` is on `PATH` and that
`kubectl cluster-info` succeeds, and reports the failure in the UI rather than
crashing.

These match the repo's own defaults in `make/common.mk` — release
`observability`, namespace `default` — so obs-ctl and
`make deploy-observability` / `make uninstall-observability` are
interchangeable. obs-ctl does not pass `--set dashboardUi.image.tag` /
`analytics.image.tag` the way `make` does, but the chart already defaults both
to `demo`, so the result is the same.

## Configuration

All constants live in `config.go`:

| Constant | Value |
| --- | --- |
| `ReleaseName` | `observability` |
| `Namespace` | `default` |
| `ChartPath` | `../infra/helm/observability` |
| `DashboardURL` | `http://localhost:8082` |
| `LicenseKey` | `0000` |

## Verifying a removal

After removing the plane (`2`), confirm the application is unaffected:

```bash
make verify-teardown
```

See [`demo/`](../demo/README.md) for what that checks.

## Known limitations

- **Relative chart path.** Running from anywhere other than `obs-ctl/` fails.
  `portforward.go` likewise sets `cmd.Dir = ".."`.
- **The license key never reaches Helm.** It is validated in the CLI only; the
  observability chart has no `licenseKey` value to set. `helm.go` keeps the
  parameter reserved for when it does.
- **Fixed dashboard delay.** `o` sleeps 3 seconds for the port-forward rather
  than polling for readiness.
- **No detection of an existing port-forward**, so `o` fails if `:8082` is
  already held — e.g. by `make port-forward-dashboard-ui`.
- **A compiled `obs-ctl` binary is committed** to this directory. Prefer
  rebuilding it locally; it should not be trusted as up to date.
