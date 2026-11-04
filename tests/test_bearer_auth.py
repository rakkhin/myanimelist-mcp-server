import json
from contextlib import asynccontextmanager

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from main import create_server
from utils.bearer_auth import BearerTokenMiddleware, bearer_middleware

TOKEN = "0123456789abcdef0123456789abcdef"


def protected_app(calls):
    async def endpoint(request):
        calls.append(request.url.path)
        return PlainTextResponse("ok")

    return Starlette(
        routes=[Route("/mal", endpoint)],
        middleware=[Middleware(BearerTokenMiddleware, token=TOKEN)],
    )


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_valid_token_reaches_the_app(scheme):
    calls = []
    client = TestClient(protected_app(calls))

    response = client.get(
        "/mal", headers={"Authorization": f"{scheme} {TOKEN}"}
    )

    assert response.status_code == 200
    assert calls == ["/mal"]


@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param(TOKEN, id="no-scheme"),
        pytest.param(f"Basic {TOKEN}", id="other-scheme"),
        pytest.param("Bearer", id="no-token"),
        pytest.param(f"Bearer {TOKEN[:-1]}", id="prefix"),
        pytest.param(f"Bearer {TOKEN}0", id="longer"),
        pytest.param(f"Bearer  {TOKEN}", id="two-spaces"),
        pytest.param(f"Bearer {TOKEN.upper()}", id="other-case"),
        pytest.param(f"Bearer {TOKEN[:-1]}é", id="not-ascii"),
    ],
)
def test_request_without_the_token_is_refused(authorization):
    calls = []
    client = TestClient(protected_app(calls))
    headers = {"Authorization": authorization} if authorization else {}
    if authorization is not None and not authorization.isascii():
        # httpx refuses to encode non-ASCII str headers itself.
        headers = {"Authorization": authorization.encode("latin-1")}

    response = client.get("/mal", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert calls == []


@pytest.mark.parametrize("valid_first", [True, False])
def test_request_with_several_authorization_headers_is_refused(valid_first):
    calls = []
    client = TestClient(protected_app(calls))
    values = [f"Bearer {TOKEN}", "Bearer other"]
    if not valid_first:
        values.reverse()

    response = client.get(
        "/mal", headers=[("Authorization", value) for value in values]
    )

    assert response.status_code == 401
    assert calls == []


def test_lifespan_reaches_the_app():
    started = []

    @asynccontextmanager
    async def lifespan(app):
        started.append(True)
        yield

    app = Starlette(
        lifespan=lifespan,
        middleware=[Middleware(BearerTokenMiddleware, token=TOKEN)],
    )

    with TestClient(app):
        pass

    assert started == [True]


def test_bearer_middleware_wraps_the_token():
    middleware = bearer_middleware(TOKEN)

    assert middleware.cls is BearerTokenMiddleware
    assert middleware.kwargs == {"token": TOKEN}


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


def test_mcp_server_behind_bearer_check(monkeypatch):
    monkeypatch.setenv("MAL_CLIENT_ID", "test-id")
    app = create_server().http_app(
        path="/mal", middleware=[bearer_middleware(TOKEN)]
    )
    valid = {"Authorization": f"Bearer {TOKEN}"}

    with TestClient(app) as client:
        allowed = _initialize(client, "/mal", valid)
        refused = _initialize(client, "/mal", {})
        elsewhere = _initialize(client, "/mcp", valid)

    assert allowed.status_code == 200
    assert '"name":"myanimelist"' in allowed.text.replace(" ", "")
    assert refused.status_code == 401
    assert elsewhere.status_code == 404
