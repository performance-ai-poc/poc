# Helm Templates

Kubernetes manifests rendered by the `ai-chat` Helm chart.

Current template groups:

- `customer-ui-*` - deployment and service for the customer-facing UI.
- `dashboard-ui-*` - deployment and service for the dashboard UI.
- `orchestrator-*` - deployment and service for the FastAPI orchestrator.
- `mcp-server-*` - deployment and service for the FastMCP server.
- `postgres-*` - persistent PostgreSQL workload, Service, and Secret.
- `mcp-seed-job.yaml` - first-install database schema, data, and read-only role setup.
- `_helpers.tpl` - shared chart naming helpers.

Prefer changing defaults in `values.yaml` when a setting should be
environment-configurable. Edit the templates when the Kubernetes resource
shape itself changes.
