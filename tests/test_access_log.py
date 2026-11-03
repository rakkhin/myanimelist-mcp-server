import json
import logging

import anyio
import pytest

from utils.access_log import RPCMethodFilter, RPCMethodMiddleware, rpc_method


def access_record() -> logging.LogRecord:
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0, "", None, None
    )
    assert RPCMethodFilter().filter(record)
    return record


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', "tools/list"),
        (
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
            "notifications/initialized",
        ),
        # A client's response to a server request names no method.
        (b'{"jsonrpc":"2.0","id":1,"result":{}}', "-"),
        (b"", "-"),
        (b"not json", "-"),
        (b"\xff", "-"),
        (b'[{"method":"tools/list"}]', "-"),
        (b'{"method":1}', "-"),
        (b'{"method":""}', "-"),
        (b"[" * 100_000, "-"),
        (
            json.dumps({"method": "x\nINFO: forged"}).encode(),
            "'x\\nINFO: forged'",
        ),
        (json.dumps({"method": "\x1b[31m"}).encode(), "'\\x1b[31m'"),
        (
            json.dumps(
                {
                    "method": "tools/call",
                    "params": {"name": "get_anime", "arguments": {"q": "x"}},
                }
            ).encode(),
            "tools/call get_anime",
        ),
        (b'{"method":"tools/call"}', "tools/call"),
        (b'{"method":"tools/call","params":[]}', "tools/call"),
        (b'{"method":"tools/call","params":{"name":1}}', "tools/call"),
        (b'{"method":"tools/call","params":{"name":""}}', "tools/call"),
        (
            json.dumps(
                {"method": "tools/call", "params": {"name": "a\nINFO: b"}}
            ).encode(),
            "tools/call 'a\\nINFO: b'",
        ),
        # Only tool calls get a name.
        (b'{"method":"prompts/get","params":{"name":"p"}}', "prompts/get"),
    ],
)
def test_rpc_method(body, expected):
    assert rpc_method(body) == expected


def http_scope():
    return {"type": "http", "method": "POST", "path": "/mcp"}


def run_app(app, chunks):
    """Call app with a request body in chunks; return what the log saw."""
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": i < len(chunks) - 1,
        }
        for i, chunk in enumerate(chunks)
    ]
    logged = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        # uvicorn writes the access log when the response starts.
        if message["type"] == "http.response.start":
            logged.append(access_record().rpc_method)

    anyio.run(RPCMethodMiddleware(app), http_scope(), receive, send)
    return logged


async def read_body(receive):
    more_body = True
    while more_body:
        message = await receive()
        more_body = message.get("more_body", False)


async def respond(send):
    await send({"type": "http.response.start", "status": 200})
    await send({"type": "http.response.body", "body": b""})


def test_logs_method_of_request_read_in_chunks():
    async def app(scope, receive, send):
        await read_body(receive)
        await respond(send)

    logged = run_app(app, [b'{"method":', b'"tools/call"}'])

    assert logged == ["tools/call"]


def test_logs_method_when_response_is_sent_from_a_task():
    # Streaming (SSE) responses are sent from tasks the app starts.
    async def app(scope, receive, send):
        await read_body(receive)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(respond, send)

    logged = run_app(app, [b'{"method":"initialize"}'])

    assert logged == ["initialize"]


def test_unread_body_is_logged_without_method():
    async def app(scope, receive, send):
        await respond(send)

    logged = run_app(app, [b'{"method":"tools/list"}'])

    assert logged == ["-"]


def test_records_outside_a_request_have_no_method():
    async def app(scope, receive, send):
        await read_body(receive)
        await respond(send)

    run_app(app, [b'{"method":"tools/list"}'])

    assert access_record().rpc_method == "-"


def test_other_scopes_pass_through_unchanged():
    seen = []

    async def app(scope, receive, send):
        seen.append((scope, receive, send))

    async def receive():
        raise AssertionError("not called")

    async def send(message):
        raise AssertionError("not called")

    scope = {"type": "lifespan"}
    anyio.run(RPCMethodMiddleware(app), scope, receive, send)

    assert seen == [(scope, receive, send)]
