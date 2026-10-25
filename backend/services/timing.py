"""Per-request stage timings (ms) for the voice pipeline, collected via a context variable."""
import contextvars
import time
from contextlib import contextmanager
from typing import Optional

_current: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("timings", default=None)


def begin() -> dict:
    d: dict = {}
    _current.set(d)
    return d


def add(name: str, ms: float) -> None:
    d = _current.get()
    if d is not None:
        d[name] = round(d.get(name, 0) + ms)


@contextmanager
def stage(name: str):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        add(name, (time.perf_counter() - t0) * 1000)


def current() -> dict:
    return dict(_current.get() or {})
