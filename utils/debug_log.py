"""Log every HTTP request in full, for debugging clients (MAL_HTTP_DEBUG).

Each request is written as one JSON line to the "mal.http.debug" logger:
client address, method, path, query string, every header and the body.
The values of credential headers (CREDENTIAL_HEADERS) are replaced by their
length, after the authentication scheme for Authorization, which is enough
to tell whether a client sends the right kind of token. Query parameters
keep their names, but their values are replaced by their length too, since
some clients put tokens there. The body is logged as sent, tool arguments
included, up to the body limit (MAL_HTTP_DEBUG_BODY_LIMIT bytes).

The middleware reads the body up to that limit before passing the request
on, so that requests the authentication checks refuse are logged with their
bodies too, then replays it to the app unchanged, followed by the rest of
the body. The limit keeps clients, authenticated or not, from making the
server hold large bodies in memory. JSON escapes line breaks and control
characters, so clients cannot forge log lines.
"""

from __future__ import annotations

import json
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOGGER_NAME = "mal.http.debug"
logger = logging.getLogger(LOGGER_NAME)

DEFAULT_BODY_LIMIT = 128 * 1024

# Lowercase, as ASGI servers pass header names.
CREDENTIAL_HEADERS = frozenset(
    (
        "authorization",
        "proxy-authorization",
        "cookie",
        "cf-access-jwt-assertion",
        "cf-access-client-secret",
    )
)
# Headers whose value starts with an authentication scheme (RFC 9110).
_SCHEME_HEADERS = frozenset(("authorization", "proxy-authorization"))


def _latin1(value: bytes) -> str:
    # ASGI servers pass header names and values and the query string as
    # bytes; latin-1 maps every byte, as in Starlette's Headers.
    return value.decode("latin-1")


def _header(name: bytes, value: bytes) -> list[str]:
    name, value = _latin1(name), _latin1(value)
    if name.lower() not in CREDENTIAL_HEADERS:
        return [name, value]
    scheme, space, credentials = value.partition(" ")
    if name.lower() in _SCHEME_HEADERS and space:
        return [name, f"{scheme} <redacted, {len(credentials)} chars>"]
    return [name, f"<redacted, {len(value)} chars>"]


def _query(query_string: bytes) -> str:
    # Keep the query's shape and names as sent; only the values are hidden.
    parts = []
    for part in _latin1(query_string).split("&"):
        name, equals, value = part.partition("=")
        parts.append(
            f"{name}=<redacted, {len(value)} chars>" if equals else name
        )
    return "&".join(parts)


def request_record(scope: Scope, body: bytes, *, body_truncated: bool) -> str:
    """Describe a request as one JSON line."""
    client = scope.get("client")
    return json.dumps(
        {
            "client": f"{client[0]}:{client[1]}" if client else None,
            "method": scope["method"],
            "scheme": scope.get("scheme", "http"),
            "http_version": scope.get("http_version", "1.1"),
            "path": scope["path"],
            "query": _query(scope.get("query_string", b"")),
            "headers": [
                _header(name, value) for name, value in scope["headers"]
            ],
            "body": body.decode("utf-8", errors="backslashreplace"),
            "body_truncated": body_truncated,
        },
        ensure_ascii=False,
    )


class RequestDebugMiddleware:
    """Log each HTTP request, headers and body included, before handling it."""

    def __init__(
        self, app: ASGIApp, body_limit: int = DEFAULT_BODY_LIMIT
    ) -> None:
        self.app = app
        self.body_limit = body_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        received: list[Message] = []
        body = bytearray()
        while True:
            message = await receive()
            received.append(message)
            if message["type"] != "http.request":
                break  # http.disconnect: the client went away.
            body.extend(message.get("body", b""))
            # Past the limit, the app reads the rest from the server.
            if not message.get("more_body", False) or (
                len(body) > self.body_limit
            ):
                break
        logger.debug(
            "Request %s",
            request_record(
                scope,
                bytes(body[: self.body_limit]),
                body_truncated=len(body) > self.body_limit,
            ),
        )

        async def replay() -> Message:
            if received:
                return received.pop(0)
            return await receive()

        await self.app(scope, replay, send)
