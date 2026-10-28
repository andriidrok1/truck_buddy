"""Hours of service from the driver's own Motive (ex-KeepTruckin) account.

The driver pastes a personal API key from the Motive dashboard into the app; it travels only as the
X-Motive-Key header of copilot requests, is never stored or logged here, and is used for one call:
GET /v1/available_time, which returns the remaining drive / shift / cycle time in seconds.
HOS_MOTIVE=0 turns the intent off.
"""
import logging
import os
from typing import Optional

from pydantic import BaseModel

from services.http import client

logger = logging.getLogger("truckbuddy.hos")

URL = "https://api.gomotive.com/v1/available_time"
TIMEOUT_S = 6
LOW_DRIVE_MIN = 30
CONNECT = "Connect Motive in settings and I'll keep track of your hours."
BAD_KEY = "Motive didn't accept the key. Check it in settings."
DOWN = "I can't reach Motive right now. Try again in a minute."
LOW = "Only {m} minutes of drive time left, let's find you a place to stop."
FULL = "You've got {drive} of drive time, {shift} on the shift, and {cycle} left on the cycle."
OUT = "You're out of drive time. Let's find you a place to stop."


class Hos(BaseModel):
    drive_min: int
    shift_min: int
    cycle_min: int
    break_min: Optional[int] = None  # minutes until a 30-minute break is due
    driver: Optional[str] = None


class HosAuthError(Exception):
    pass


class HosDown(Exception):
    pass


def _minutes(seconds) -> int:
    try:
        return max(0, int(round(float(seconds) / 60)))
    except (TypeError, ValueError):
        return 0


def parse(data: dict) -> Optional[Hos]:
    """Motive available_time body -> Hos for the first user, or None when the shape is unexpected."""
    users = (data or {}).get("users") if isinstance(data, dict) else None
    if not isinstance(users, list) or not users:
        return None
    u = users[0] if isinstance(users[0], dict) else {}
    avail = u.get("available_time") or {}
    if not isinstance(avail, dict) or not any(k in avail for k in ("drive", "shift", "cycle")):
        return None
    who = u.get("user") or {}
    name = " ".join(x for x in (who.get("first_name"), who.get("last_name")) if isinstance(x, str)).strip() or None
    return Hos(drive_min=_minutes(avail.get("drive")), shift_min=_minutes(avail.get("shift")),
               cycle_min=_minutes(avail.get("cycle")),
               break_min=_minutes(avail["break"]) if avail.get("break") is not None else None, driver=name)


async def available_time(key: str) -> Hos:
    try:
        r = await client().get(URL, headers={"X-Api-Key": key, "Accept": "application/json"}, timeout=TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        raise HosDown(type(e).__name__) from e
    if r.status_code in (401, 403):
        raise HosAuthError(str(r.status_code))
    if r.is_error:
        raise HosDown(f"HTTP {r.status_code}")
    try:
        hos = parse(r.json())
    except ValueError as e:
        raise HosDown("bad json") from e
    if hos is None:
        raise HosDown("unexpected shape")
    return hos


def _hm(minutes: int) -> str:
    h, m = divmod(max(0, minutes), 60)
    if h and m:
        return f"{h} hour{'s' if h != 1 else ''} {m} minutes"
    if h:
        return f"{h} hour{'s' if h != 1 else ''}"
    return f"{m} minutes"


def speech(hos: Hos) -> str:
    if hos.drive_min <= 0:
        return OUT
    if hos.drive_min < LOW_DRIVE_MIN:
        return LOW.format(m=hos.drive_min)
    return FULL.format(drive=_hm(hos.drive_min), shift=_hm(hos.shift_min), cycle=_hm(hos.cycle_min))


def enabled() -> bool:
    return os.environ.get("HOS_MOTIVE", "1").strip().lower() in ("1", "true", "yes", "on")
