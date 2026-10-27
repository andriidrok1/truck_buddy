"""Live truck parking counts (TPIMS) and public-lot capacity (NTAD).

TPIMS: the Midwest states publish MAASTO-format JSON for sensor-equipped rest areas and truck stops:
a static list (site, coordinates, capacity) and a dynamic one (available spaces, trend, open) that
updates every few minutes. Three feeds are open without a key (WI, KY, IL); TPIMS_FEEDS can add
more as JSON {"state": [dynamic_url, static_url]}. Polled every 2 minutes while the app runs.

NTAD: FHWA's one-time survey of ~1,900 public facilities with the number of truck spaces
(backend/data/truck_parking_capacity.json, scripts/import_ntad.py) for "18 spots there, no live count".

A place gets a count when a TPIMS site is within 300 m of it, else a capacity when an NTAD facility
is within 500 m. TPIMS_ENABLED=0 turns the poller off (capacity still works).
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel

from models import Place
from services.flags import on
from services.geo import haversine_km
from services.http import client

logger = logging.getLogger("truckbuddy.tpims")

DEFAULT_FEEDS = {
    "WI": ("https://511wi.gov/api/TPIMS_Dynamic", "https://511wi.gov/api/TPIMS_Static"),
    "KY": ("http://www.trimarc.org/dat/tpims/TPIMS_Dynamic.json", "http://www.trimarc.org/dat/tpims/TPIMS_Static.json"),
    "IL": ("https://truckparking.travelmidwest.com/TPIMS_Dynamic.json",
           "https://truckparking.travelmidwest.com/TPIMS_Static.json"),
}
POLL_S = 120
STATIC_TTL_S = 24 * 3600
STALE_MIN = 15
TIMEOUT_S = 10
LIVE_WITHIN_M = 300
CAPACITY_WITHIN_M = 1000  # NTAD pins are approximate (one point for both directions of a rest area)
LIVE_OPEN = " {n} truck spots open there right now."
LIVE_LOW = " Parking's almost full there."
LIVE_FULL = " Parking's full there right now."
CAPACITY_ONLY = " {cap} truck spots there, no live count."


class Site(BaseModel):
    site_id: str
    name: str
    lat: float
    lng: float
    capacity: Optional[int] = None
    highway: Optional[str] = None
    exit: Optional[str] = None
    direction: Optional[str] = None
    state: Optional[str] = None


class Live(BaseModel):
    site_id: str
    available: Optional[int] = None  # None = unknown / LOW / stale
    low: bool = False
    trend: Optional[str] = None  # FILLING | CLEARING | STEADY
    open: bool = True
    capacity: Optional[int] = None
    updated: Optional[datetime] = None
    stale: bool = False


_sites: dict[str, Site] = {}
_live: dict[str, Live] = {}
_static_at: dict[str, float] = {}
_task: Optional[asyncio.Task] = None


def feeds() -> dict[str, tuple[str, str]]:
    raw = os.environ.get("TPIMS_FEEDS")
    if not raw:
        return dict(DEFAULT_FEEDS)
    try:
        extra = json.loads(raw)
        out = dict(DEFAULT_FEEDS)
        for k, v in (extra or {}).items():
            if isinstance(v, (list, tuple)) and len(v) == 2:
                out[str(k).upper()] = (str(v[0]), str(v[1]))
        return out
    except ValueError:
        logger.warning("TPIMS_FEEDS is not JSON; using the defaults")
        return dict(DEFAULT_FEEDS)


def _ts(raw) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_static(data, state: Optional[str] = None) -> dict[str, Site]:
    out: dict[str, Site] = {}
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict) or not row.get("siteId"):
            continue
        loc = row.get("location") or {}
        try:
            lat, lng = float(loc.get("latitude")), float(loc.get("longitude"))
        except (TypeError, ValueError):
            continue
        cap = row.get("capacity")
        out[str(row["siteId"])] = Site(
            site_id=str(row["siteId"]), name=str(row.get("name") or "Truck parking").strip(), lat=lat, lng=lng,
            capacity=int(cap) if isinstance(cap, (int, float)) and cap > 0 else None,
            highway=str(row.get("relevantHighway") or "").strip() or None,
            exit=str(row.get("exitID") or "").strip() or None,
            direction=str(row.get("directionOfTravel") or "").strip() or None,
            state=str(loc.get("state") or state or "").strip().upper() or None)
    return out


def parse_dynamic(data, now: Optional[datetime] = None) -> dict[str, Live]:
    """reportedAvailable: a number (int or numeric string), "LOW", or "Unknown"; a report older than
    STALE_MIN minutes is kept but marked stale with no count."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, Live] = {}
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict) or not row.get("siteId"):
            continue
        raw = row.get("reportedAvailable")
        available: Optional[int] = None
        low = False
        if isinstance(raw, bool):
            raw = None
        if isinstance(raw, (int, float)):
            available = max(0, int(raw))
        elif isinstance(raw, str):
            s = raw.strip().upper()
            if s.isdigit():
                available = int(s)
            elif s == "LOW":
                low = True
        updated = _ts(row.get("timeStamp"))
        stale = updated is None or (now - updated) > timedelta(minutes=STALE_MIN)
        cap = row.get("capacity")
        out[str(row["siteId"])] = Live(
            site_id=str(row["siteId"]), available=None if stale else available, low=low and not stale,
            trend=str(row.get("trend")).upper() if isinstance(row.get("trend"), str) and
            str(row.get("trend")).upper() in ("FILLING", "CLEARING", "STEADY") else None,
            open=row.get("open") is not False, capacity=int(cap) if isinstance(cap, (int, float)) and cap > 0 else None,
            updated=updated, stale=stale)
    return out


async def _get_json(url: str):
    r = await client().get(url, timeout=TIMEOUT_S)
    if r.is_error:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


async def refresh(now: Optional[float] = None) -> None:
    """One poll of every feed; a failed feed keeps whatever it had before."""
    mono = time.monotonic() if now is None else now
    for state, (dyn_url, static_url) in feeds().items():
        if mono - _static_at.get(state, -1e9) >= STATIC_TTL_S:
            try:
                sites = parse_static(await _get_json(static_url), state)
                if sites:
                    _sites.update(sites)
                    _static_at[state] = mono
            except Exception as e:  # noqa: BLE001
                logger.warning("TPIMS static %s failed: %s", state, e)
        try:
            live = parse_dynamic(await _get_json(dyn_url))
            if live:
                _live.update(live)
        except Exception as e:  # noqa: BLE001
            logger.warning("TPIMS dynamic %s failed: %s", state, e)


async def _poll() -> None:
    while True:
        try:
            await refresh()
        except Exception as e:  # noqa: BLE001
            logger.warning("TPIMS poll failed: %s", e)
        await asyncio.sleep(POLL_S)


async def start() -> None:
    global _task
    if on("TPIMS_ENABLED") and _task is None:
        _task = asyncio.create_task(_poll())


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None


def nearest(lat: float, lng: float, within_m: float = LIVE_WITHIN_M) -> Optional[Site]:
    best, best_km = None, within_m / 1000
    for s in _sites.values():
        km = haversine_km(lat, lng, s.lat, s.lng)
        if km <= best_km:
            best, best_km = s, km
    return best


def capacity_near(lat: float, lng: float, within_m: float = CAPACITY_WITHIN_M) -> Optional[int]:
    """Truck spaces of the closest NTAD public facility within `within_m`, else None."""
    from services.places import data_rows
    best, best_km = None, within_m / 1000
    for r in data_rows("truck_parking_capacity"):
        try:
            km = haversine_km(lat, lng, float(r["lat"]), float(r["lng"]))
        except (KeyError, TypeError, ValueError):
            continue
        if km <= best_km and isinstance(r.get("spots"), int):
            best, best_km = r["spots"], km
    return best


def attach(places: list[Place]) -> None:
    """Fills the parking_* fields of truck stops and rest areas from TPIMS (live) or NTAD (capacity)."""
    for p in places:
        if p.category not in ("truck_stop", "rest_area"):
            continue
        site = nearest(p.lat, p.lng)
        if site:
            live = _live.get(site.site_id)
            p.parking_source = "tpims"
            p.parking_capacity = (live.capacity if live and live.capacity else None) or site.capacity
            if live and not live.stale and live.open:
                p.parking_available, p.parking_low, p.parking_trend = live.available, live.low, live.trend
                p.parking_updated = live.updated.isoformat() if live.updated else None
            continue
        cap = capacity_near(p.lat, p.lng)
        if cap:
            p.parking_source, p.parking_capacity = "ntad", cap


def sentence(place: Place) -> str:
    """Spoken parking line for a route reply: live count, almost full, full, capacity only, or nothing."""
    if place.category not in ("truck_stop", "rest_area"):
        return ""
    if place.parking_available is not None:
        return LIVE_FULL if place.parking_available == 0 else LIVE_OPEN.format(n=place.parking_available)
    if place.parking_low:
        return LIVE_LOW
    if place.parking_capacity:
        return CAPACITY_ONLY.format(cap=place.parking_capacity)
    return ""
