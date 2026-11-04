"""Log every HTTP request in full, for debugging clients (MAL_HTTP_DEBUG).

Each request is written as one JSON line to the "mal.http.debug" logger:
client address, method, path, query string, every header and the body.
Nothing is redacted, so the log holds the credentials clients send (bearer
tokens, Access JWTs and service token secrets): turn it on only while
debugging, and treat the log as a secret.

The middleware reads the whole body before passing the request on, so that
requests the authentication checks refuse are logged with their bodies too,
then replays it to the app unchanged. JSON escapes line breaks and control
characters, so clients cannot forge log lines.
"""

from __future__ import annotations

import json
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOGGER_NAME = "mal.http.debug"
logger = logging.getLogger(LOGGER_NAME)


def _latin1(value: bytes) -> str:
    # ASGI servers pass header names and values and the query string as
    # bytes; latin-1 maps every byte, as in Starlette's Headers.
    return value.decode("latin-1")


def request_record(scope: Scope, body: bytes) -> str:
    """Describe a request as one JSON line."""
    client = scope.get("client")
    return json.dumps(
        {
            "client": f"{client[0]}:{client[1]}" if client else None,
            "method": scope["method"],
            "scheme": scope.get("scheme", "http"),
            "http_version": scope.get("http_version", "1.1"),
            "path": scope["path"],
            "query": _latin1(scope.get("query_string", b"")),
            "headers": [
                [_latin1(name), _latin1(value)]
                for name, value in scope["headers"]
            ],
            "body": body.decode("utf-8", errors="backslashreplace"),
        },
        ensure_ascii=False,
    )


class RequestDebugMiddleware:
    """Log each HTTP request, headers and body included, before handling it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

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
            if not message.get("more_body", False):
                break
        logger.debug("Request %s", request_record(scope, bytes(body)))

        async def replay() -> Message:
            if received:
                return received.pop(0)
            return await receive()

        await self.app(scope, replay, send)
