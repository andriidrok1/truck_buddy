"""Where a truck can get into a place, from OpenStreetMap: the points where a service road or driveway meets a
public road (or a highway ramp, for rest areas). Coordinates come from the map, never from the vision model; the
model only picks among these points (QA 2026-11-02: free-form vision coordinates landed on lawns and roofs).
"""
import asyncio
import logging
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from services import kv
from services.http import client

logger = logging.getLogger("truckbuddy.osm_access")

MIRRORS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter")
RADIUS_M = 260  # covers the corners of the 400 m frame
PUBLIC = {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary",
          "tertiary_link", "unclassified", "residential", "living_street", "road"}
MERGE_M = 15.0  # two junctions closer than this are one driveway
MAX_POINTS = 14
CACHE_MAX = 500
STORE_DAYS = 90  # driveways change rarely; the store row is refetched after this
STORE_PREFIX = "osm:"  # rows in feed_cache: the junction list of one place (a few hundred bytes)


@dataclass
class Access:
    lat: float
    lng: float
    road: str  # name (or ref) of the public road the driveway leaves; "" when unnamed
    ramp: bool = False  # a highway ramp into a rest area, not a driveway
    private: bool = False


def _m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    dy = (b_lat - a_lat) * 111320
    dx = (b_lng - a_lng) * 111320 * math.cos(math.radians(a_lat))
    return math.hypot(dx, dy)


async def fetch(lat: float, lng: float, timeout: float = 60) -> Optional[dict]:
    q = f'[out:json][timeout:50];way(around:{RADIUS_M},{lat},{lng})[highway];(._;>;);out body;'
    for url in MIRRORS:
        try:
            r = await client().post(url, data={"data": q}, timeout=timeout,
                                    headers={"User-Agent": "TruckBuddy/1.0 (parking access)"})
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - try the next mirror
            logger.warning("overpass %s failed: %s", url, e)
    return None


def points(osm: dict, lat: float, lng: float, half_m: float = 195.0) -> list[Access]:
    """Driveway junctions inside the square frame around (lat, lng), nearest first."""
    nodes, ways = {}, []
    for e in osm.get("elements", []):
        if e["type"] == "node":
            nodes[e["id"]] = (e["lat"], e["lon"])
        elif e["type"] == "way":
            ways.append(e)
    public: dict[int, str] = {}
    ramp: set[int] = set()
    service: dict[int, bool] = {}
    for w in ways:
        t = w.get("tags", {})
        hw = t.get("highway")
        if hw in PUBLIC:
            for n in w["nodes"]:
                public.setdefault(n, t.get("name") or t.get("ref") or "")
        elif hw == "motorway_link":
            # Only the end a ramp flows INTO is a way in; the other end is the way back onto the highway
            # (Elkhorn, eval 2026-11-02: the model picked the exit ramp).
            ramp.add(w["nodes"][0] if t.get("oneway") == "-1" else w["nodes"][-1])
        elif hw == "service" and t.get("service") != "drive-through":
            private = t.get("access") in ("private", "no")
            for n in w["nodes"]:
                service[n] = service.get(n, False) or private
    out: list[Access] = []
    for n, private in service.items():
        if n not in nodes or (n not in public and n not in ramp):
            continue
        p_lat, p_lng = nodes[n]
        dy = (p_lat - lat) * 111320
        dx = (p_lng - lng) * 111320 * math.cos(math.radians(lat))
        if abs(dx) > half_m or abs(dy) > half_m:
            continue
        if any(_m(a.lat, a.lng, p_lat, p_lng) < MERGE_M for a in out):
            continue
        out.append(Access(lat=p_lat, lng=p_lng, road=public.get(n, ""), ramp=n not in public, private=private))
    out.sort(key=lambda a: _m(lat, lng, a.lat, a.lng))
    return out[:MAX_POINTS]


_points: dict[str, list[Access]] = {}
_loading: dict[str, "asyncio.Task[list[Access]]"] = {}


async def around(lat: float, lng: float, wait: float) -> list[Access]:
    """Driveway junctions around a place: memory, then the store (survives restarts, shared by instances), then
    Overpass. Waits at most `wait` s ([] then); the load keeps running in the background, so the next check of
    the place (its one re-check) gets the markers."""
    k = f"{lat:.5f},{lng:.5f}"
    if k in _points:
        return _points[k]
    task = _loading.get(k)
    if task is None:
        task = asyncio.create_task(_load(k, lat, lng))
        _loading[k] = task
        task.add_done_callback(lambda _t: _loading.pop(k, None))
    try:
        return await asyncio.wait_for(asyncio.shield(task), wait)
    except asyncio.TimeoutError:
        return []


async def _load(k: str, lat: float, lng: float) -> list[Access]:
    pts = await _stored(k)
    if pts is None:
        osm = await fetch(lat, lng, timeout=45)
        if osm is None:
            return []
        pts = points(osm, lat, lng)
        try:
            await kv.feed_put(STORE_PREFIX + k, [asdict(a) for a in pts])
        except Exception as e:  # noqa: BLE001 - the store is a cache
            logger.warning("osm store write failed for %s: %s", k, e)
    if len(_points) >= CACHE_MAX:
        _points.pop(next(iter(_points)))
    _points[k] = pts
    return pts


async def _stored(k: str) -> Optional[list[Access]]:
    try:
        row = await kv.feed_get(STORE_PREFIX + k)
    except Exception as e:  # noqa: BLE001
        logger.warning("osm store read failed for %s: %s", k, e)
        return None
    if row is None or datetime.now(timezone.utc) - row[1] > timedelta(days=STORE_DAYS):
        return None
    try:
        return [Access(**a) for a in row[0]]
    except TypeError:
        return None
