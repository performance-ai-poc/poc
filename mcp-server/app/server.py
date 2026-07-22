"""FastMCP server entrypoint.

Starts a streamable-HTTP MCP server on port 8000 and registers the seven mock
tools (see ``app/tools``) plus the original demo ``add`` tool. Correlated JSON
logging is configured at import so ``mcp.request``/``mcp.response`` lines land on
stdout from the first tool call.
"""

from mcp.server.fastmcp import FastMCP

from app.logging_utils import configure_logging
from app.tools import register

configure_logging()

mcp = FastMCP(
    "baseline-mcp-server",
    host="0.0.0.0",
    port=8000,
    json_response=True,
)


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers and return the result."""
    return a + b


# Registers get_schema, run_query, search_documents, list_endpoints, http_get,
# http_post, fail_next — each wrapped with correlated logging + error mapping.
register(mcp)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
