import os
import re
import signal
import sys
from pathlib import Path

from fastmcp import FastMCP
from starlette.middleware import Middleware

from tools.tools import register_tools
from utils.access_log import LOG_CONFIG, RPCMethodMiddleware
from utils.bearer_auth import bearer_middleware
from utils.cloudflare_access import access_middleware
from utils.debug_log import RequestDebugMiddleware

_TRANSPORTS = ("stdio", "http")
# RFC 6750 b64token, so that clients can send the token unchanged.
_BEARER_TOKEN = re.compile(r"[A-Za-z0-9\-._~+/]+=*")
_MIN_BEARER_TOKEN_LENGTH = 32
_FLAGS = {
    **dict.fromkeys(("1", "true", "yes", "on"), True),
    **dict.fromkeys(("", "0", "false", "no", "off"), False),
}


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


def _bearer_token() -> str | None:
    """Read the static token HTTP requests must carry, if one is set.

    The token comes from MAL_HTTP_BEARER_TOKEN or from the file named by
    MAL_HTTP_BEARER_TOKEN_FILE (such as a Docker secret), not both. Error
    messages never include the token.
    """
    token = os.getenv("MAL_HTTP_BEARER_TOKEN")
    path = os.getenv("MAL_HTTP_BEARER_TOKEN_FILE")
    if token and path:
        raise ValueError(
            "Set MAL_HTTP_BEARER_TOKEN or MAL_HTTP_BEARER_TOKEN_FILE, not both"
        )
    if path:
        name = "MAL_HTTP_BEARER_TOKEN_FILE"
        try:
            # Files usually end with a newline.
            token = Path(path).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(
                f"{name}: cannot read the token from {path!r}: {error}"
            ) from error
    elif token:
        name = "MAL_HTTP_BEARER_TOKEN"
    else:
        return None
    if len(token) < _MIN_BEARER_TOKEN_LENGTH or not _BEARER_TOKEN.fullmatch(
        token
    ):
        raise ValueError(
            f"{name} must give a token of at least "
            f"{_MIN_BEARER_TOKEN_LENGTH} characters from A-Z, a-z, 0-9 and "
            "-._~+/ (optionally ending in =), such as the output of "
            "`openssl rand -hex 32`"
        )
    return token


def _http_debug() -> bool:
    """Read whether to log every HTTP request in full (MAL_HTTP_DEBUG)."""
    value = os.getenv("MAL_HTTP_DEBUG", "")
    try:
        return _FLAGS[value.strip().lower()]
    except KeyError:
        raise ValueError(
            "MAL_HTTP_DEBUG must be one of 1, true, yes, on, 0, false, no, "
            f"off, got {value!r}"
        ) from None


def main():
    # Validate the settings before anything starts listening.
    transport = _transport()
    if transport == "http":
        settings = _http_settings()
        access = _access_settings()
        bearer_token = _bearer_token()
        middleware = [Middleware(RPCMethodMiddleware)]
        if _http_debug():
            # First, so that requests the checks refuse are logged too.
            middleware.insert(0, Middleware(RequestDebugMiddleware))
            print(
                "MAL_HTTP_DEBUG is on: logging every HTTP request in full, "
                "including the credentials it carries.",
                file=sys.stderr,
            )
        if access:
            middleware.append(access_middleware(*access))
        if bearer_token:
            middleware.append(bearer_middleware(bearer_token))
        if not access and not bearer_token:
            print(
                "Serving HTTP without authentication (neither "
                "MAL_HTTP_BEARER_TOKEN[_FILE] nor MAL_CF_ACCESS_* is set); "
                "requests must be authenticated in front of this server.",
                file=sys.stderr,
            )
        # uvicorn handles SIGINT and SIGTERM itself.
        create_server().run(
            transport="http",
            middleware=middleware,
            uvicorn_config={"log_config": LOG_CONFIG},
            **settings,
        )
        return

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _handle_shutdown_signal)
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
