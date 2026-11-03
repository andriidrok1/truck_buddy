"""A request id per HTTP request (X-Request-Id in, generated when absent, echoed out), readable anywhere in the
request's task tree, and stamped onto every log record as %(req)s so a driver's complaint, the turn line and the
trip events can be joined (server-side trip events carry it as data.req).

An incoming id is used only when it looks like one (letters, digits, dot, underscore, dash; at most 64): it
lands in every log line and in stored events, so anything else is replaced by a fresh id."""
import contextvars
import logging
import re
import uuid
from typing import Optional

from starlette.types import ASGIApp, Receive, Scope, Send

_current: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("request_id", default=None)
HEADER = b"x-request-id"
VALID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
logger = logging.getLogger("truckbuddy.http")


def current() -> Optional[str]:
    return _current.get()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def accepted(incoming: str) -> str:
    """The client's id when it is well formed, else a new one."""
    return incoming if VALID.match(incoming) else new_id()


class RequestIdMiddleware:
    """Pure ASGI (no body buffering): sets the id for the request's context and adds it to the response headers.

    It is the outermost user middleware, so an unhandled exception is caught here, while the id is still set:
    the traceback is logged with it and the 500 carries X-Request-Id (Starlette's ServerErrorMiddleware sits
    outside, after the id is gone). An error after the response started is re-raised for the server to abort
    the connection."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == HEADER), "")
        rid = accepted(incoming)
        token = _current.set(rid)
        started = False

        async def send_with_id(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers", []))
                headers.append((HEADER, rid.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            logger.exception("unhandled error: %s %s", scope.get("method"), scope.get("path"))
            if started:
                raise
            body = b"Internal Server Error"
            await send_with_id({"type": "http.response.start", "status": 500,
                                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                            (b"content-length", str(len(body)).encode())]})
            await send_with_id({"type": "http.response.body", "body": body})
        finally:
            _current.reset(token)


_installed = False


def install_log_field() -> None:
    """Every LogRecord gets `req` (\"-\" outside a request), so a format string with %(req)s works for any handler,
    uvicorn's and pytest's included (a handler filter would miss records that reach other handlers)."""
    global _installed
    if _installed:
        return
    base = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        record.req = current() or "-"
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
