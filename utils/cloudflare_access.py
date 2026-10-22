"""Require a Cloudflare Access JWT on every HTTP request.

The HTTP transport is meant to run behind Cloudflare Access, which adds a
signed JWT to each request it lets through. Checking that JWT here also
refuses requests that reach the port some other way: another local process,
a misconfigured tunnel route, or a web page in a browser on the same machine
(DNS rebinding).
"""

from __future__ import annotations

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"


def access_jwt_verifier(team_domain: str, audience: str) -> JWTVerifier:
    """Verify the JWTs Access issues for one application.

    Access signs with keys it rotates; JWTVerifier fetches them from the
    team's certs endpoint and refetches when it meets an unknown key id.
    """
    return JWTVerifier(
        jwks_uri=f"{team_domain}/cdn-cgi/access/certs",
        issuer=team_domain,
        audience=audience,
        algorithm="RS256",
    )


class AccessJWTMiddleware:
    """Answer 403 to every request without a valid Access JWT."""

    def __init__(self, app: ASGIApp, verifier: TokenVerifier) -> None:
        self.app = app
        self.verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Lifespan events carry no request, and the app needs them to start.
        if scope["type"] != "lifespan":
            token = Headers(scope=scope).get(ACCESS_JWT_HEADER)
            if not token or await self.verifier.verify_token(token) is None:
                response = PlainTextResponse("Forbidden", status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def access_middleware(team_domain: str, audience: str) -> Middleware:
    return Middleware(
        AccessJWTMiddleware,
        verifier=access_jwt_verifier(team_domain, audience),
    )
