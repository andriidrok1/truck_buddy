"""Daily budgets for what costs money, so free drivers in the first release cannot run up the bill.

- TURNS_PER_DAY (default 150): copilot turns per driver per day (/agent and the voice turn), each an LLM call or
  several plus speech. The general request limits in services/auth.py count every request (map, nudges, health)
  and only guard against a leaked token; this one is about the AI bill.
- SAT_NEW_PER_DAY (default 300): new satellite parking checks per day for the whole service (a vision call each,
  ~$0.03); a place checked before is answered from the shared cache and does not count.

The day is the US Pacific calendar day, so budgets come back at midnight Pacific. Counters live in this worker's
memory and each worker gets its share of the budget (settings().per_worker), like the request limits; a restart
forgets them, which errs on the driver's side. Tester tokens are not counted. 0 turns a budget off.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from services.settings import settings

PACIFIC = ZoneInfo("America/Los_Angeles")
TURNS_DEFAULT = 150
SAT_DEFAULT = 300
OUT_OF_TURNS = ("That's all the free questions for today. They come back at midnight Pacific. "
                "The map and the route keep working.")
SAT_SKIPPED = "Daily satellite checks are used up; I'll check this one tomorrow."

_counts: dict[tuple[str, str, str], int] = {}  # (kind, who, pacific day) -> used


def _budget(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def day(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(PACIFIC)).astimezone(PACIFIC).date().isoformat()


def _take(kind: str, who: str, total: int, now: Optional[datetime]) -> bool:
    if total <= 0:
        return True
    today = day(now)
    if len(_counts) > 50_000:  # yesterday's counters
        for k in [k for k in _counts if k[2] != today]:
            del _counts[k]
    key = (kind, who, today)
    if _counts.get(key, 0) >= settings().per_worker(total):
        return False
    _counts[key] = _counts.get(key, 0) + 1
    return True


def take_turn(driver: Optional[str], role: str = "driver", now: Optional[datetime] = None) -> bool:
    """One copilot turn from the driver's daily budget; False when it is spent (the caller says OUT_OF_TURNS)."""
    if not driver or role == "tester":
        return True
    return _take("turn", driver, _budget("TURNS_PER_DAY", TURNS_DEFAULT), now)


def take_satellite(now: Optional[datetime] = None) -> bool:
    """One new satellite check from today's service-wide budget."""
    return _take("sat", "*", _budget("SAT_NEW_PER_DAY", SAT_DEFAULT), now)


def reset() -> None:
    _counts.clear()
