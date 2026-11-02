"""A request id per HTTP request (X-Request-Id in, generated when absent, echoed out), readable anywhere in the
request's task tree, and stamped onto every log record as %(req)s so a driver's complaint, the turn line and the
trip events can be joined (server-side trip events carry it as data.req)."""
import contextvars
import logging
import uuid
from typing import Optional

from starlette.types import ASGIApp, Receive, Scope, Send

_current: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("request_id", default=None)
HEADER = b"x-request-id"


def current() -> Optional[str]:
    return _current.get()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class RequestIdMiddleware:
    """Pure ASGI (no body buffering): sets the id for the request's context and adds it to the response headers."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == HEADER), "")
        rid = incoming[:64] if incoming.strip() else new_id()
        token = _current.set(rid)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER, rid.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
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
