"""Add the JSON-RPC method to uvicorn's access log.

Every MCP request is a POST to the same path, so uvicorn's access log line
alone ("POST /mcp HTTP/1.1" 200 OK) does not say what the client asked for.
RPCMethodMiddleware keeps the body of each request as the app reads it, and
RPCMethodFilter adds the method it names (initialize, tools/list, ...) to the
access log record, which uvicorn writes when the app starts the response.

The body reaches the filter through a context variable: the app sends the
response from its own call or from tasks it starts, which inherit the
context. Requests answered before the middleware, such as those FastMCP's
Host and Origin check refuses, are still logged, with "-" as the method.
"""

from __future__ import annotations

import copy
import json
import logging
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uvicorn.config import LOGGING_CONFIG

from utils.debug_log import LOGGER_NAME as DEBUG_LOGGER_NAME

_request_body: ContextVar[bytearray | None] = ContextVar(
    "request_body", default=None
)


def _printable(value: str) -> str:
    # The client chose it: escape anything that could forge log lines.
    return value if value.isprintable() else repr(value)


def rpc_method(body: bytes | bytearray) -> str:
    """Return the method of a JSON-RPC request body, or "-" without one.

    For tools/call, the name of the tool follows the method.
    """
    try:
        message = json.loads(body)
    except (ValueError, RecursionError):
        return "-"
    method = message.get("method") if isinstance(message, dict) else None
    if not isinstance(method, str) or not method:
        return "-"
    params = message.get("params")
    if method == "tools/call" and isinstance(params, dict):
        name = params.get("name")
        if isinstance(name, str) and name:
            return f"{method} {_printable(name)}"
    return _printable(method)


class RPCMethodMiddleware:
    """Keep each HTTP request's body for RPCMethodFilter."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        body = bytearray()

        async def receive_and_keep() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
            return message

        token = _request_body.set(body)
        try:
            await self.app(scope, receive_and_keep, send)
        finally:
            _request_body.reset(token)


class RPCMethodFilter(logging.Filter):
    """Set rpc_method on access log records from the current request."""

    def filter(self, record: logging.LogRecord) -> bool:
        body = _request_body.get()
        record.rpc_method = "-" if body is None else rpc_method(body)
        return True


# uvicorn's logging setup with the method at the end of each access log line.
LOG_CONFIG = copy.deepcopy(LOGGING_CONFIG)
LOG_CONFIG["filters"] = {"rpc_method": {"()": RPCMethodFilter}}
LOG_CONFIG["handlers"]["access"]["filters"] = ["rpc_method"]
LOG_CONFIG["formatters"]["access"]["fmt"] += " %(rpc_method)s"
# The full request log (MAL_HTTP_DEBUG) goes to stderr, like uvicorn's own
# messages. It only has records when RequestDebugMiddleware is installed.
LOG_CONFIG["loggers"][DEBUG_LOGGER_NAME] = {
    "handlers": ["default"],
    "level": "DEBUG",
    "propagate": False,
}
