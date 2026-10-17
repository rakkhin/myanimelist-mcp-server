import signal
import sys

from fastmcp import FastMCP

from tools.tools import register_tools


def create_server() -> FastMCP:
    """Create and configure the MyAnimeList MCP server."""
    mcp = FastMCP("myanimelist", mask_error_details=True)
    register_tools(mcp)
    return mcp


def _handle_sigint(sig, frame):
    # Diagnostics go to stderr so they never corrupt the stdio JSON-RPC stream.
    print("Shutting down server gracefully...", file=sys.stderr)
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, _handle_sigint)
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
