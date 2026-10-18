import os
import signal
import sys

from fastmcp import FastMCP

from tools.tools import register_tools


def create_server() -> FastMCP:
    """Create and configure the MyAnimeList MCP server."""
    mcp = FastMCP("myanimelist", mask_error_details=True)
    register_tools(mcp)
    return mcp


def _handle_shutdown_signal(signum, frame):
    """Exit at once on SIGINT or SIGTERM.

    The stdio transport reads stdin with a blocking call in a non-daemon
    worker thread that cannot be cancelled, so unwinding normally
    (KeyboardInterrupt, sys.exit) waits until the next line arrives on stdin.
    Clients stop the server by closing stdin, which shuts it down cleanly;
    signals skip the unwinding. Nothing is lost: there is no cleanup to run
    and token files are replaced atomically.
    """
    # Diagnostics go to stderr so they never corrupt the stdio JSON-RPC stream.
    name = signal.Signals(signum).name
    print(f"Received {name}, shutting down.", file=sys.stderr, flush=True)
    os._exit(128 + signum)


def main():
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _handle_shutdown_signal)
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
