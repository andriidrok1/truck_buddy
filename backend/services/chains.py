"""Chain controls on California mountain passes, from Caltrans' public CWWP2 feed (no key, public domain).

One JSON per district, refreshed every minute by Caltrans; we poll the Sierra/Shasta districts every
10 minutes (CHAIN_DISTRICTS, default 2,3,6,9,10) and match active controls (R-1..R-3) to a route by
distance. Routes that never enter California cost nothing. CHAIN_CONTROLS=0 turns it all off.

R-1: chains or snow tires required. R-2: chains required on all vehicles except 4WD/AWD with snow
tires (for a truck: chains). R-3: chains on all vehicles, no exceptions.
"""
import asyncio
import logging
import os
import time
from typing import Optional

from models import ChainControl
from services.flags import on
from services.geo import haversine_km
from services import kv, leader
from services.http import client

logger = logging.getLogger("truckbuddy.chains")

URL = "https://cwwp2.dot.ca.gov/data/d{n}/cc/ccStatusD{n:02d}.json"
DEFAULT_DISTRICTS = "2,3,6,9,10"
TTL_S = 600
POLL_S = 600
TIMEOUT_S = 8
WITHIN_KM = 3.0
SAMPLE_KM = 2.0
CA_BBOX = (32.5, 42.1, -124.6, -114.0)  # lat min/max, lng min/max
ACTIVE = ("R-1", "R-2", "R-3")
LINE = {"R-1": "Chains or snow tires required on {route} near {location}.",
        "R-2": "Heads up, chains required on {route} near {location}.",
        "R-3": "Heads up, chains required on every vehicle, {route} near {location}."}
MORE = " And {n} more chain control{s} along the route."

_cache: dict[int, tuple[float, list[ChainControl]]] = {}  # district -> (fetched_at, controls)
_task: Optional[asyncio.Task] = None


def districts() -> list[int]:
    raw = os.environ.get("CHAIN_DISTRICTS", DEFAULT_DISTRICTS)
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 12:
            out.append(int(part))
    return out


def parse(data) -> list[ChainControl]:
    """Caltrans cc records -> ChainControl list; rows without coordinates or a status are skipped."""
    out: list[ChainControl] = []
    rows = (data or {}).get("data") if isinstance(data, dict) else None
    for row in rows if isinstance(rows, list) else []:
        cc = (row or {}).get("cc") if isinstance(row, dict) else None
        if not isinstance(cc, dict):
            continue
        loc = cc.get("location") or {}
        status = (cc.get("statusData") or {}).get("status")
        try:
            lat, lng = float(loc.get("latitude")), float(loc.get("longitude"))
        except (TypeError, ValueError):
            continue
        if not isinstance(status, str) or not status:
            continue
        ts = (cc.get("statusData") or {}).get("statusTimestamp") or {}
        updated = " ".join(x for x in (ts.get("statusDate"), ts.get("statusTime")) if x) or None
        route = str(loc.get("route") or "").strip() or "the highway"
        out.append(ChainControl(
            id=str(cc.get("index") or f"{route}@{lat:.4f},{lng:.4f}"), route=route,
            location=str(loc.get("locationName") or loc.get("nearbyPlace") or route).strip(),
            nearby=str(loc.get("nearbyPlace") or "").strip() or None, lat=lat, lng=lng,
            direction=str(loc.get("direction") or "").strip() or None, status=status.strip().upper(),
            description=str((cc.get("statusData") or {}).get("statusDescription") or "").strip()[:200],
            updated=updated, in_service=str(cc.get("inService", "true")).lower() != "false"))
    return out


async def fetch_district(n: int, now: Optional[float] = None) -> list[ChainControl]:
    """Current controls of a district; on any failure the last good copy stays (and is returned)."""
    now = time.monotonic() if now is None else now
    hit = _cache.get(n)
    if hit and now - hit[0] < TTL_S:
        return hit[1]
    try:
        r = await client().get(URL.format(n=n), timeout=TIMEOUT_S)
        if r.is_error:
            raise RuntimeError(f"HTTP {r.status_code}")
        controls = parse(r.json())
    except Exception as e:  # noqa: BLE001
        logger.warning("Caltrans chain controls D%02d failed: %s", n, e)
        return hit[1] if hit else []
    _cache[n] = (now, controls)
    return controls


FEED_NAME = "caltrans_chains"


async def refresh_all() -> None:
    await asyncio.gather(*(fetch_district(n) for n in districts()), return_exceptions=True)


async def share() -> None:
    """The leader's copy of every district into feed_cache for the other workers."""
    payload = {str(n): [c.model_dump(mode="json") for c in controls] for n, (_at, controls) in _cache.items()}
    await kv.feed_put(FEED_NAME, payload)


async def take_shared(now: Optional[float] = None) -> bool:
    """A follower: the leader's districts from feed_cache into the local cache. False when there is none yet."""
    row = await kv.feed_get(FEED_NAME)
    if not row:
        return False
    payload, _fetched = row
    mono = time.monotonic() if now is None else now
    for n, items in (payload or {}).items():
        try:
            _cache[int(n)] = (mono, [ChainControl(**c) for c in items])
        except Exception as e:  # noqa: BLE001
            logger.warning("shared chain controls D%s unreadable: %s", n, e)
    return True


async def _poll() -> None:
    while True:
        try:
            if await leader.try_lead(FEED_NAME):
                await refresh_all()
                await share()
            elif not await take_shared():
                await refresh_all()  # no leader copy yet: fetch once ourselves
        except Exception as e:  # noqa: BLE001
            logger.warning("chain control poll failed: %s", e)
        await asyncio.sleep(POLL_S)


async def start() -> None:
    global _task
    if on("CHAIN_CONTROLS") and _task is None:
        _task = asyncio.create_task(_poll())


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None


def touches_california(geometry: list[list[float]]) -> bool:
    return any(CA_BBOX[0] <= p[0] <= CA_BBOX[1] and CA_BBOX[2] <= p[1] <= CA_BBOX[3] for p in geometry)


def along_route(geometry: list[list[float]], controls: list[ChainControl],
                within_km: float = WITHIN_KM) -> list[ChainControl]:
    """Active controls within `within_km` of the line, each with the first geometry index it is near,
    in route order."""
    active = [c for c in controls if c.status in ACTIVE and c.in_service]
    if not active or len(geometry) < 1:
        return []
    from services.weather import sample_points
    found: dict[tuple, ChainControl] = {}  # Caltrans lists one physical point several times (190, 190a, ...)
    for idx, lat, lng in sample_points(geometry, every_km=SAMPLE_KM, max_samples=100_000):
        for c in active:
            key = (c.route, c.location, c.direction, c.status)
            if key in found:
                continue
            if haversine_km(lat, lng, c.lat, c.lng) <= within_km:
                found[key] = c.model_copy(update={"index": idx})
    return sorted(found.values(), key=lambda c: c.index)


async def for_route(geometry: list[list[float]]) -> list[ChainControl]:
    """Chain controls on a route; [] outside California, when off, or when Caltrans is unreachable."""
    if not on("CHAIN_CONTROLS") or not touches_california(geometry):
        return []
    lists = await asyncio.gather(*(fetch_district(n) for n in districts()), return_exceptions=True)
    controls = [c for lst in lists if isinstance(lst, list) for c in lst]
    return along_route(geometry, controls)


def speech(controls: list[ChainControl]) -> str:
    if not controls:
        return ""
    first = controls[0]
    line = LINE.get(first.status, LINE["R-2"]).format(route=first.route, location=first.location)
    if len(controls) > 1:
        n = len(controls) - 1
        line += MORE.format(n=n, s="" if n == 1 else "s")
    return line
