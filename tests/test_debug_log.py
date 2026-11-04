import json
import logging

import anyio
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from utils.access_log import LOG_CONFIG
from utils.bearer_auth import BearerTokenMiddleware
from utils.debug_log import LOGGER_NAME, RequestDebugMiddleware

TOKEN = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def records(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    def logged():
        return [
            json.loads(r.getMessage().removeprefix("Request "))
            for r in caplog.records
            if r.name == LOGGER_NAME
        ]

    return logged


def echo_app(*middleware):
    async def endpoint(request):
        return PlainTextResponse(await request.body())

    return Starlette(
        routes=[Route("/mal", endpoint, methods=["GET", "POST"])],
        middleware=[Middleware(RequestDebugMiddleware), *middleware],
    )


def test_logs_the_whole_request_and_passes_the_body_on(records):
    client = TestClient(echo_app())
    body = '{"method":"tools/call","params":{"q":"進撃"}}\nINFO: forged'

    response = client.post(
        "/mal?a=1&b=2",
        content=body.encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "X-Two": "x"},
    )

    assert response.text == body
    (record,) = records()
    headers = record.pop("headers")
    assert record == {
        "client": "testclient:50000",
        "method": "POST",
        "scheme": "http",
        "http_version": "1.1",
        "path": "/mal",
        "query": "a=1&b=2",
        "body": body,
    }
    # Credentials are logged as sent.
    assert ["authorization", f"Bearer {TOKEN}"] in headers
    assert ["x-two", "x"] in headers


def test_record_is_one_line(caplog, records):
    client = TestClient(echo_app())

    client.post("/mal", content=b"a\nb\r\x1b[31m\xff")

    (record,) = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert "\n" not in record.getMessage()
    assert "\x1b" not in record.getMessage()
    assert records()[0]["body"] == "a\nb\r\x1b[31m\\xff"


def test_logs_requests_the_checks_refuse(records):
    client = TestClient(
        echo_app(Middleware(BearerTokenMiddleware, token=TOKEN))
    )

    response = client.post("/mal", content=b"secret body")

    assert response.status_code == 401
    assert records()[0]["body"] == "secret body"


def test_logs_requests_without_body(records):
    client = TestClient(echo_app())

    response = client.get("/mal")

    assert response.text == ""
    assert records()[0]["method"] == "GET"
    assert records()[0]["body"] == ""


def call_middleware(scope, messages):
    """Run the middleware on raw ASGI messages; return what the app read."""
    read = []
    # The middleware takes the messages from the list before the app runs.
    count = len(messages) + 1

    async def app(scope, receive, send):
        for _ in range(count):
            read.append(await receive())

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):  # pragma: no cover - the app sends nothing
        pass

    anyio.run(RequestDebugMiddleware(app), scope, receive, send)
    return read


HTTP_SCOPE = {
    "type": "http",
    "method": "POST",
    "path": "/mal",
    "headers": [(b"x-raw", b"\xe9")],
}


def test_body_in_several_chunks_is_logged_and_replayed(records):
    chunks = [
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.request", "body": b"cd", "more_body": False},
    ]

    read = call_middleware(HTTP_SCOPE, list(chunks))

    # The app gets the same messages, then reads from the server again.
    assert read == [*chunks, {"type": "http.disconnect"}]
    (record,) = records()
    assert record["body"] == "abcd"
    assert record["client"] is None
    assert record["query"] == ""
    assert record["headers"] == [["x-raw", "é"]]


def test_client_leaving_mid_body_is_logged_and_replayed(records):
    messages = [
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.disconnect"},
    ]

    read = call_middleware(HTTP_SCOPE, list(messages))

    assert read[:2] == messages
    assert records()[0]["body"] == "ab"


def test_other_scopes_pass_through(records):
    read = call_middleware({"type": "lifespan"}, [{"type": "x"}])

    assert read[0] == {"type": "x"}
    assert records() == []


def test_log_config_writes_the_debug_log_to_stderr():
    assert LOG_CONFIG["loggers"][LOGGER_NAME] == {
        "handlers": ["default"],
        "level": "DEBUG",
        "propagate": False,
    }
    assert LOG_CONFIG["handlers"]["default"]["stream"] == "ext://sys.stderr"
