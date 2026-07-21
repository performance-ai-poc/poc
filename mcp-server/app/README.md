# MCP Server App

Python package containing the FastMCP server implementation.

Current files:

- `server.py` - creates the `FastMCP` instance, registers the demo `add` tool,
  and starts the server with `streamable-http` when run as `python -m app.server`.
- `__init__.py` - package marker.

Keep tool implementations small and explicit here until there is enough
behavior to justify splitting them into modules.
