"""Require a static bearer token on every HTTP request.

For servers that are not behind Cloudflare Access, or as a second check
behind it: every request must carry ``Authorization: Bearer <token>`` with
the configured token. Like the Access check, it also refuses requests that
reach the port some other way (another local process, DNS rebinding).

It is ASGI middleware rather than a FastMCP auth provider: FastMCP's bearer
auth publishes OAuth discovery metadata, which makes clients try to sign in
through OAuth instead of sending the token they were given.
"""

from __future__ import annotations

import hmac

from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenMiddleware:
    """Answer 401 to every request without the configured bearer token."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token.encode("ascii")

    def _authorized(self, headers: Headers) -> bool:
        # The scheme is case-insensitive (RFC 7235).
        scheme, _, token = headers.get("authorization", "").partition(" ")
        # Starlette decodes header values as latin-1, so this cannot fail;
        # compare_digest takes the same time wherever the tokens differ.
        return scheme.lower() == "bearer" and hmac.compare_digest(
            token.encode("latin-1"), self.token
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Lifespan events carry no request, and the app needs them to start.
        if scope["type"] != "lifespan" and not self._authorized(
            Headers(scope=scope)
        ):
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def bearer_middleware(token: str) -> Middleware:
    return Middleware(BearerTokenMiddleware, token=token)
