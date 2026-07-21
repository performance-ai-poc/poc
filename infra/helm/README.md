# Helm

Helm chart workspace for the AI chat POC.

Current charts:

- `ai-chat/` - application chart for the customer UI, dashboard UI,
  orchestrator service, and MCP server.

Run chart-level validation from the repo root:

```bash
make helm-lint
make helm-template
```
