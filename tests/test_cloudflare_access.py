import json
from contextlib import asynccontextmanager

import pytest
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from main import create_server
from utils.cloudflare_access import (
    ACCESS_JWT_HEADER,
    AccessJWTMiddleware,
    access_jwt_verifier,
    access_middleware,
)

TEAM = "https://team.cloudflareaccess.com"
AUD = "0123abcd"


@pytest.fixture(scope="module")
def key():
    return RSAKeyPair.generate()


@pytest.fixture
def verifier(key):
    return JWTVerifier(public_key=key.public_key, issuer=TEAM, audience=AUD)


def access_jwt(key, **overrides):
    # Access sends the audience as a list.
    claims = {"issuer": TEAM, "audience": [AUD]} | overrides
    return key.create_token(**claims)


def protected_app(verifier, calls):
    async def endpoint(request):
        calls.append(request.url.path)
        return PlainTextResponse("ok")

    return Starlette(
        routes=[Route("/mal", endpoint)],
        middleware=[Middleware(AccessJWTMiddleware, verifier=verifier)],
    )


def test_valid_jwt_reaches_the_app(key, verifier):
    calls = []
    client = TestClient(protected_app(verifier, calls))

    response = client.get("/mal", headers={ACCESS_JWT_HEADER: access_jwt(key)})

    assert response.status_code == 200
    assert calls == ["/mal"]


@pytest.mark.parametrize(
    "make_token",
    [
        pytest.param(lambda key: None, id="missing"),
        pytest.param(lambda key: "not-a-jwt", id="malformed"),
        pytest.param(
            lambda key: access_jwt(RSAKeyPair.generate()), id="other-key"
        ),
        pytest.param(
            lambda key: access_jwt(key, audience=["other-app"]),
            id="other-audience",
        ),
        pytest.param(
            lambda key: access_jwt(
                key, issuer="https://other.cloudflareaccess.com"
            ),
            id="other-issuer",
        ),
        pytest.param(
            lambda key: access_jwt(key, expires_in_seconds=-60), id="expired"
        ),
    ],
)
def test_request_without_valid_jwt_is_refused(key, verifier, make_token):
    calls = []
    client = TestClient(protected_app(verifier, calls))
    token = make_token(key)
    headers = {ACCESS_JWT_HEADER: token} if token else {}

    response = client.get("/mal", headers=headers)

    assert response.status_code == 403
    assert calls == []


def test_lifespan_reaches_the_app(verifier):
    started = []

    @asynccontextmanager
    async def lifespan(app):
        started.append(True)
        yield

    app = Starlette(
        lifespan=lifespan,
        middleware=[Middleware(AccessJWTMiddleware, verifier=verifier)],
    )

    with TestClient(app):
        pass

    assert started == [True]


def test_verifier_checks_the_team_and_application():
    verifier = access_jwt_verifier(TEAM, AUD)

    assert verifier.jwks_uri == f"{TEAM}/cdn-cgi/access/certs"
    assert verifier.issuer == TEAM
    assert verifier.audience == AUD
    assert verifier.algorithm == "RS256"


def test_access_middleware_wraps_the_verifier():
    middleware = access_middleware(TEAM, AUD)

    assert middleware.cls is AccessJWTMiddleware
    assert middleware.kwargs["verifier"].issuer == TEAM


def _initialize(client, path, headers):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    return client.post(
        path,
        content=json.dumps(body),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **headers,
        },
    )


def test_mcp_server_behind_access_check(monkeypatch, key, verifier):
    monkeypatch.setenv("MAL_CLIENT_ID", "test-id")
    app = create_server().http_app(
        path="/mal",
        middleware=[Middleware(AccessJWTMiddleware, verifier=verifier)],
    )
    valid = {ACCESS_JWT_HEADER: access_jwt(key)}

    with TestClient(app) as client:
        allowed = _initialize(client, "/mal", valid)
        refused = _initialize(client, "/mal", {})
        elsewhere = _initialize(client, "/mcp", valid)

    assert allowed.status_code == 200
    assert '"name":"myanimelist"' in allowed.text.replace(" ", "")
    assert refused.status_code == 403
    assert elsewhere.status_code == 404
