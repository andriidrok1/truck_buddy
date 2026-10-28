"""Per-request stage timings (ms) and notes for the voice pipeline, collected via context variables.

Stages are summed milliseconds (`stage("llm_round_1")`). Counters use the `n_` prefix (`add("n_cached_tokens", 1536)`)
and are reported but never summed into `total`. Notes are labels for the turn log (`note("intent", "find")`).
"""
import contextvars
import time
from contextlib import contextmanager
from typing import Optional

_current: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("timings", default=None)
_notes: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("timing_notes", default=None)

COUNTER_PREFIX = "n_"


def begin() -> dict:
    d: dict = {}
    _current.set(d)
    _notes.set({})
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


def note(name: str, value) -> None:
    """A label for the turn log (intent, status, error kind); ignored outside a begun request."""
    n = _notes.get()
    if n is not None:
        n[name] = value


def current() -> dict:
    return dict(_current.get() or {})


def notes() -> dict:
    return dict(_notes.get() or {})


def is_counter(name: str) -> bool:
    return name.startswith(COUNTER_PREFIX)
