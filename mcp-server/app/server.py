from mcp.server.fastmcp import FastMCP

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


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
