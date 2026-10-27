"""Feature flags from the environment, read at call time so Railway can flip one without a deploy.

    from services.flags import on
    if on("ROUTE_INCIDENTS"):  # default on; set ROUTE_INCIDENTS=0 to turn off

A flag is on for "1", "true", "yes", "on" (case-insensitive) and off for anything else.
"""
import os

_TRUE = {"1", "true", "yes", "on"}


def on(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in _TRUE
