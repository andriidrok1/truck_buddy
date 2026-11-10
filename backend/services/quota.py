"""Daily budgets for what costs money, so free drivers in the first release cannot run up the bill.

- TURNS_PER_DAY (default 150): copilot turns per driver per day (/agent and the voice turn), each an LLM call or
  several plus speech. The general request limits in services/auth.py count every request (map, nudges, health)
  and only guard against a leaked token; this one is about the AI bill.
- NEW_DEVICE_TURNS (default 30): the turn budget instead of TURNS_PER_DAY while the device is less than a day old
  (its driver row's created_at, services/tenancy.device_created), so a farm of fresh installs gets little each.
- SAT_NEW_PER_DAY (default 300): new satellite parking checks per day for the whole service (a vision call each,
  ~$0.03); a place checked before is answered from the shared cache and does not count. No new checks at all once
  the day's money is mostly spent (services/spend.py, stage no_satellite).

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
NEW_DEVICE_TURNS_DEFAULT = 30
NEW_DEVICE_S = 24 * 3600
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


def turn_budget(new_device: bool = False) -> int:
    """Today's turns for one driver: TURNS_PER_DAY, or NEW_DEVICE_TURNS on a device's first day (the smaller of
    the two when both are on; 0 = no limit)."""
    total = _budget("TURNS_PER_DAY", TURNS_DEFAULT)
    first_day = _budget("NEW_DEVICE_TURNS", NEW_DEVICE_TURNS_DEFAULT) if new_device else 0
    if first_day and total:
        return min(total, first_day)
    return first_day or total


def take_turn(driver: Optional[str], role: str = "driver", now: Optional[datetime] = None,
              new_device: bool = False) -> bool:
    """One copilot turn from the driver's daily budget; False when it is spent (the caller says OUT_OF_TURNS)."""
    if not driver or role == "tester":
        return True
    return _take("turn", driver, turn_budget(new_device), now)


def take_satellite(now: Optional[datetime] = None) -> bool:
    """One new satellite check from today's service-wide budget; none once spend reaches no_satellite."""
    from services import spend  # lazy: spend imports this module for the Pacific day
    if not spend.satellite_open():
        return False
    return _take("sat", "*", _budget("SAT_NEW_PER_DAY", SAT_DEFAULT), now)


def reset() -> None:
    _counts.clear()
