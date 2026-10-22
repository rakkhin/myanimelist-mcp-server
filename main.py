import os
import signal
import sys

from fastmcp import FastMCP

from tools.tools import register_tools
from utils.cloudflare_access import access_middleware

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


def _access_settings() -> tuple[str, str] | None:
    """Read which Cloudflare Access application HTTP requests must pass.

    The check is opt-in: with neither setting there is none, for servers
    behind another authentication layer. One without the other is an error,
    so a half-done setup never runs unprotected.
    """
    team_domain = (os.getenv("MAL_CF_ACCESS_TEAM_DOMAIN") or "").rstrip("/")
    audience = os.getenv("MAL_CF_ACCESS_AUD")
    if not team_domain and not audience:
        return None
    if not team_domain.startswith("https://"):
        raise ValueError(
            "MAL_CF_ACCESS_TEAM_DOMAIN must be the Access team domain, "
            f"such as https://<team>.cloudflareaccess.com, got {team_domain!r}"
        )
    if not audience:
        raise ValueError(
            "MAL_CF_ACCESS_AUD must be the AUD tag of the Access application"
        )
    return team_domain, audience


def main():
    # Validate the settings before anything starts listening.
    transport = _transport()
    if transport == "http":
        settings = _http_settings()
        access = _access_settings()
        if access:
            middleware = [access_middleware(*access)]
        else:
            middleware = []
            print(
                "Serving HTTP without the Cloudflare Access check "
                "(MAL_CF_ACCESS_TEAM_DOMAIN and MAL_CF_ACCESS_AUD are unset); "
                "requests must be authenticated in front of this server.",
                file=sys.stderr,
            )
        # uvicorn handles SIGINT and SIGTERM itself.
        create_server().run(
            transport="http", middleware=middleware, **settings
        )
        return

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _handle_shutdown_signal)
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
