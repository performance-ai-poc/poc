# MCP Server

Baseline FastMCP service for the AI chat POC.

The current implementation in `app/server.py` starts a streamable HTTP MCP
server on port `8000` and exposes one demo tool:

- `add(a: int, b: int) -> int`

## Running Locally

Requires Python 3.12+ if you want to match the Docker image.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.server
```

## Container

From the repo root:

```bash
docker build -t mcp-server:demo ./mcp-server
```

The root Makefile wraps this with:

```bash
make build-mcp
make rebuild-mcp
```

## Notes

No orchestrator integration exists yet. When real tools are added, document the
tool names, inputs, outputs, tenant/data boundaries, and expected telemetry.
