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
        "query": "a=<redacted, 1 chars>&b=<redacted, 1 chars>",
        "body": body,
        "body_truncated": False,
    }
    assert ["authorization", "Bearer <redacted, 32 chars>"] in headers
    assert ["x-two", "x"] in headers


@pytest.mark.parametrize(
    ("name", "value", "logged"),
    [
        ("Authorization", f"bearer {TOKEN}", "bearer <redacted, 32 chars>"),
        ("Authorization", f"Bearer  {TOKEN}", "Bearer <redacted, 33 chars>"),
        ("Authorization", TOKEN, "<redacted, 32 chars>"),
        ("Authorization", "", "<redacted, 0 chars>"),
        (
            "Proxy-Authorization",
            "Basic dXNlcjpwYXNz",
            "Basic <redacted, 12 chars>",
        ),
        ("Cookie", "CF_Authorization=jwt; a=b", "<redacted, 25 chars>"),
        ("Cf-Access-Jwt-Assertion", "a.b c", "<redacted, 5 chars>"),
        ("CF-Access-Client-Secret", "s3cret", "<redacted, 6 chars>"),
        # The Access client id names the service token; it is no secret.
        ("CF-Access-Client-Id", "id.access", "id.access"),
    ],
)
def test_credential_headers_are_redacted(records, name, value, logged):
    client = TestClient(echo_app())

    client.post("/mal", headers={name: value})

    assert [name.lower(), logged] in records()[0]["headers"]


@pytest.mark.parametrize(
    ("query", "logged"),
    [
        ("token=" + TOKEN, "token=<redacted, 32 chars>"),
        (
            "a=&flag&&b=x%20y=z",
            "a=<redacted, 0 chars>&flag&&b=<redacted, 7 chars>",
        ),
    ],
)
def test_query_values_are_redacted(records, query, logged):
    client = TestClient(echo_app())

    client.get(f"/mal?{query}")

    assert records()[0]["query"] == logged


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


def call_middleware(scope, messages, **options):
    """Run the middleware on raw ASGI messages.

    Return what the app read and how many messages the middleware left
    unread for it.
    """
    read = []
    unread = []
    count = len(messages) + 1

    async def app(scope, receive, send):
        unread.append(len(messages))
        for _ in range(count):
            read.append(await receive())
            if read[-1]["type"] == "http.disconnect":
                break

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):  # pragma: no cover - the app sends nothing
        pass

    middleware = RequestDebugMiddleware(app, **options)
    anyio.run(middleware, scope, receive, send)
    return read, unread[0]


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

    read, _ = call_middleware(HTTP_SCOPE, list(chunks))

    # The app gets the same messages, then reads from the server again.
    assert read == [*chunks, {"type": "http.disconnect"}]
    (record,) = records()
    assert record["body"] == "abcd"
    assert record["body_truncated"] is False
    assert record["client"] is None
    assert record["query"] == ""
    assert record["headers"] == [["x-raw", "é"]]


def test_client_leaving_mid_body_is_logged_and_replayed(records):
    messages = [
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.disconnect"},
    ]

    read, _ = call_middleware(HTTP_SCOPE, list(messages))

    assert read == messages
    assert records()[0]["body"] == "ab"


def test_body_past_the_limit_is_cut_and_the_rest_left_to_the_app(records):
    chunks = [
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.request", "body": b"cd", "more_body": True},
        {"type": "http.request", "body": b"ef", "more_body": False},
    ]

    read, unread = call_middleware(HTTP_SCOPE, list(chunks), body_limit=3)

    # It stops reading once past the limit; the app reads the rest.
    assert unread == 1
    assert read == [*chunks, {"type": "http.disconnect"}]
    (record,) = records()
    assert record["body"] == "abc"
    assert record["body_truncated"] is True


@pytest.mark.parametrize(
    ("chunks", "limit", "body", "truncated"),
    [
        ([b"abcd"], 4, "abcd", False),
        ([b"ab", b"cd", b""], 4, "abcd", False),
        ([b"abcd"], 3, "abc", True),
        ([b""], 0, "", False),
        ([b"x"], 0, "", True),
        # A character cut in two is escaped like any invalid UTF-8.
        (["é".encode()], 1, "\\xc3", True),
    ],
)
def test_body_limit(records, chunks, limit, body, truncated):
    messages = [
        {"type": "http.request", "body": chunk, "more_body": True}
        for chunk in chunks
    ]
    messages[-1]["more_body"] = False

    read, _ = call_middleware(HTTP_SCOPE, list(messages), body_limit=limit)

    assert read[: len(messages)] == messages
    (record,) = records()
    assert record["body"] == body
    assert record["body_truncated"] is truncated


def test_default_body_limit_is_128_kib():
    assert RequestDebugMiddleware(None).body_limit == 128 * 1024


def test_other_scopes_pass_through(records):
    read, _ = call_middleware({"type": "lifespan"}, [{"type": "x"}])

    assert read[0] == {"type": "x"}
    assert records() == []


def test_log_config_writes_the_debug_log_to_stderr():
    assert LOG_CONFIG["loggers"][LOGGER_NAME] == {
        "handlers": ["default"],
        "level": "DEBUG",
        "propagate": False,
    }
    assert LOG_CONFIG["handlers"]["default"]["stream"] == "ext://sys.stderr"
