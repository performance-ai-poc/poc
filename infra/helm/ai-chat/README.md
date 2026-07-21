# AI Chat Helm Chart

Application chart for the local AI chat POC deployment.

It currently deploys:

- `customer-ui` as a NodePort service on port `30080`.
- `orchestrator-svc` as a NodePort service on port `30081`.
- `dashboard-ui` as a NodePort service on port `30082`.
- `mcp-server` as a ClusterIP service on port `8000`.

Default image tags, service types, ports, and orchestrator environment values
are defined in `values.yaml`.

Useful commands from the repo root:

```bash
make helm-lint
make helm-template
make deploy
make uninstall
```
