"""Provider keys out of the logs. HERE, ORS and EIA take their key in the query string, and an httpx error message
carries the whole URL ("Client error '401 Unauthorized' for url 'https://...&apiKey=...'"), so logging the exception
put the key in the log stream (audit 2026-11-13).

describe(e) is what a call site logs for a failed provider call: the HTTP status, or the error type with its message
cleaned. RedactingFormatter is the net under everything else: every line the root handlers write, tracebacks
included, has key-like query values and Telegram bot tokens replaced with ***."""
from __future__ import annotations

import logging
import re

import httpx

# apiKey (HERE), api_key (ORS, EIA), key (Google), token / access_token / signature / sig: the value up to the next
# separator. Matched after ? or & only, so prose like "key=value" in a sentence is left alone.
_QUERY_SECRET = re.compile(r"(?i)([?&](?:api[_-]?key|key|token|access[_-]?token|client[_-]?secret|signature|sig)=)"
                           r"[^&#\s'\"]+")
_BOT_TOKEN = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")  # api.telegram.org/bot<token>/sendMessage
MASK = "***"


def redact(text: str) -> str:
    if not text:
        return text
    return _BOT_TOKEN.sub("/bot" + MASK, _QUERY_SECRET.sub(r"\1" + MASK, text))


def describe(e: BaseException) -> str:
    """A failed provider call for the log: "HTTP 403" for an error status (never the URL), else the error type and
    its message with any key masked."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    msg = redact(str(e))
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def install(logger: logging.Logger | None = None) -> None:
    """Wraps the formatter of every handler on `logger` (the root by default) so its lines are redacted."""
    for h in (logger or logging.getLogger()).handlers:
        if isinstance(h.formatter, RedactingFormatter):
            continue
        old = h.formatter or logging.Formatter()
        new = RedactingFormatter(old._fmt, old.datefmt)
        new._style = old._style  # same format style object (%, {, $) as the original
        h.setFormatter(new)
