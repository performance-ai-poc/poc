# Helm Templates

Kubernetes manifests rendered by the `ai-chat` Helm chart.

Current template groups:

- `customer-ui-*` - deployment and service for the customer-facing UI.
- `dashboard-ui-*` - deployment and service for the dashboard UI.
- `orchestrator-*` - deployment and service for the FastAPI orchestrator.
- `mcp-server-*` - deployment and service for the FastMCP server.
- `_helpers.tpl` - shared chart naming helpers.

Prefer changing defaults in `../values.yaml` when a setting should be
environment-configurable. Edit these templates when the Kubernetes resource
shape itself changes.
