# Make Targets

The root `Makefile` includes the smaller files in this folder to keep Docker,
Minikube, Helm, and per-service commands organized.

Files:

- `common.mk` - shared variables, help text, status, and aggregate build/load targets.
- `minikube.mk` - start, stop, and delete the configured Minikube profile.
- `helm.mk` - lint, template, deploy, and uninstall the Helm chart.
- `customer-ui.mk` - build, load, restart, rebuild, and logs for `customer-ui`.
- `dashboard-ui.mk` - build, load, restart, rebuild, and logs for `dashboard-ui`.
- `orchestrator.mk` - build, load, restart, rebuild, and logs for `orchestrator-svc`.
- `mcp-server.mk` - build, load, restart, rebuild, and logs for `mcp-server`.
- `port-forward.mk` - one foreground port-forward target per service.

Run from the repo root:

```bash
make help
make doctor
make dev
make status
```
