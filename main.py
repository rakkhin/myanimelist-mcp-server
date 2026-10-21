import os
import signal
import sys

from fastmcp import FastMCP

from tools.tools import register_tools

_TRANSPORTS = ("stdio", "http")


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


def _transport() -> str:
    transport = os.getenv("MAL_TRANSPORT") or "stdio"
    if transport not in _TRANSPORTS:
        raise ValueError(
            f"MAL_TRANSPORT must be one of {', '.join(_TRANSPORTS)}, "
            f"got {transport!r}"
        )
    return transport


def _http_settings() -> dict[str, str | int]:
    """Read where the HTTP transport listens.

    The host defaults to loopback: the server is meant to be reached only
    through a local tunnel connector or reverse proxy.
    """
    port = os.getenv("MAL_HTTP_PORT") or "8000"
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(
            f"MAL_HTTP_PORT must be a port number (1-65535), got {port!r}"
        )
    path = os.getenv("MAL_HTTP_PATH") or "/mcp"
    if not path.startswith("/"):
        raise ValueError(f"MAL_HTTP_PATH must start with '/', got {path!r}")
    return {
        "host": os.getenv("MAL_HTTP_HOST") or "127.0.0.1",
        "port": int(port),
        "path": path,
    }


def main():
    # Validate the settings before anything starts listening.
    transport = _transport()
    if transport == "http":
        # uvicorn handles SIGINT and SIGTERM itself.
        settings = _http_settings()
        create_server().run(transport="http", **settings)
        return

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _handle_shutdown_signal)
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
