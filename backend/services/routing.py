"""Truck routing.

Order: HERE Routing v8 (transportMode=truck) when HERE_API_KEY is set, then
OpenRouteService driving-hgv when ORS_API_KEY is set. With no key configured at all the
route is a MOCK curved line (local demo); when a key is set and every provider fails,
route() raises RoutingUnavailable so the driver hears that routing is down instead of a
line that isn't a road.
The truck profile (height, length, weight, hazmat) is sent to the real providers so
the route avoids low bridges and weight/length/hazmat restrictions.
"""
import logging
import math
import os
import re
from typing import List, Optional

import flexpolyline
from services.http import client

from models import Incident, Maneuver, RouteInfo, SpeedLimit, TrafficSegment, TruckProfile
from services.approach import from_route
from services.flags import on
from services.geo import haversine_km, km_to_mi

logger = logging.getLogger("truckbuddy.routing")

ROAD_FACTOR = 1.3
AVG_SPEED_MPH = 32.0
POINTS = 32
MAX_POINTS = 1500  # keep the payload small for long trips
HERE_URL = "https://router.hereapi.com/v8/routes"
ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-hgv/geojson"
HERE_HAZMAT = "explosive,gas,flammable,combustible,poison,corrosive"


def format_summary(duration_min: int, distance_mi: float) -> str:
    if duration_min >= 60:
        h, m = divmod(duration_min, 60)
        time = f"{h} h {m} min" if m else f"{h} h"
    else:
        time = f"{duration_min} min"
    dist = f"{distance_mi:,.0f} mi" if distance_mi >= 100 else f"{distance_mi:.1f} mi"
    return f"{time} · {dist}"


def _thin(points: List[List[float]]) -> List[List[float]]:
    if len(points) <= MAX_POINTS:
        return points
    step = math.ceil(len(points) / MAX_POINTS)
    return points[::step] + [points[-1]]


def _thin_index(off: int, n: int) -> int:
    """Index into _thin(points) of the point at offset `off` in the original n points.

    Mirrors _thin exactly: with step > 1 the thinned list is points[::step] + [points[-1]], so the
    last original point lands at a dedicated final slot; anything else maps to off // step.
    """
    if n <= 0:
        return 0
    off = max(0, min(off, n - 1))
    step = math.ceil(n / MAX_POINTS) if n > MAX_POINTS else 1
    last = (n - 1) // step + (1 if step > 1 else 0)
    return last if off == n - 1 else min(off // step, last)


def _span_ranges(spans: List[dict], n_points: int):
    """(start, end, span) for every HERE span of one section; end is the offset of the next span.

    The span's `offset` is an index into the section polyline; the span covers points
    [start, end] inclusive, where end is the next span's offset or the last point.
    """
    for i, sp in enumerate(spans):
        start = int(sp.get("offset", 0))
        end = int(spans[i + 1].get("offset", n_points - 1)) if i + 1 < len(spans) else n_points - 1
        yield start, end, sp


def _info(geometry: List[List[float]], meters: float, seconds: float, source: str, notices: List[str],
          truck_aware: bool, traffic: Optional[List[TrafficSegment]] = None) -> RouteInfo:
    mi = km_to_mi(meters / 1000)
    minutes = max(1, round(seconds / 60))
    return RouteInfo(geometry=_thin(geometry), distance_m=round(meters), duration_s=round(seconds),
                     distance_mi=round(mi, 1), duration_min=minutes, summary=format_summary(minutes, mi),
                     source=source, notices=notices, truck_aware=truck_aware, traffic=traffic or [])


def _traffic_level(span: dict) -> Optional[str]:
    info = span.get("dynamicSpeedInfo") or {}
    base, now = info.get("baseSpeed"), info.get("trafficSpeed")
    if not base or now is None:
        return None
    ratio = now / base
    return "jam" if ratio < 0.5 else "slow" if ratio < 0.75 else None


def _traffic(points: List[List[float]], spans: List[dict]) -> List[TrafficSegment]:
    """Merge consecutive slow/jam spans of one section into polylines."""
    out: List[TrafficSegment] = []
    for start, end, sp in _span_ranges(spans, len(points)):
        level = _traffic_level(sp)
        if not level:
            continue
        seg = points[start:end + 1]
        if len(seg) < 2:
            continue
        if out and out[-1].level == level and out[-1].points[-1] == seg[0]:
            out[-1].points += seg[1:]
        else:
            out.append(TrafficSegment(level=level, points=seg))
    for t in out:
        t.points = _thin(t.points)
    return out


HERE_RETURN = "polyline,summary,actions,instructions"
HERE_SPANS = "dynamicSpeedInfo"
MPH_PER_MS = 2.23694
INCIDENT_TEXT_MAX = 80


def _here_params(t: TruckProfile, origin: tuple[float, float], dest: tuple[float, float]) -> list[tuple[str, str]]:
    """Query for HERE Routing v8 as (key, value) pairs, so a key can repeat (via=...&via=...)."""
    ret, spans = [HERE_RETURN], [HERE_SPANS]
    if on("ROUTE_SPEED_LIMITS"):
        spans.append("maxSpeed")
    if on("ROUTE_INCIDENTS"):
        ret.append("incidents")
        spans.append("incidents")
    params: list[tuple[str, str]] = [
        ("transportMode", "truck"),
        ("origin", f"{origin[0]},{origin[1]}"),
        ("destination", f"{dest[0]},{dest[1]}"),
        ("return", ",".join(ret)),
        ("lang", "en-US"),
        ("units", "imperial"),
        ("spans", ",".join(spans)),
        ("vehicle[height]", str(round(t.height_in * 2.54))),  # cm
        ("vehicle[length]", str(round(t.total_length_ft * 30.48))),  # cm
        ("vehicle[grossWeight]", str(round(t.weight_lb * 0.4536))),  # kg
    ]
    if t.hazmat:
        params.append(("vehicle[shippedHazardousGoods]", HERE_HAZMAT))
    avoid = (["tollRoad"] if t.avoid_tolls else []) + (["ferry", "tunnel"] if t.avoid_ferries_tunnels else [])
    if avoid:
        params.append(("avoid[features]", ",".join(avoid)))
    return params


def _parse_route(route: dict) -> RouteInfo:
    """One HERE route object -> RouteInfo (pure; no I/O). Sections are concatenated in order."""
    sections = route.get("sections") or []
    if not sections:
        raise RuntimeError("HERE route has no sections")
    geometry, meters, seconds, raw, traffic, actions = [], 0.0, 0.0, [], [], []
    limits: list[tuple[int, int]] = []  # (global offset, mph)
    incidents: list[tuple[int, dict]] = []  # (global offset, incident)
    for s in sections:
        pts = [[round(p[0], 6), round(p[1], 6)] for p in flexpolyline.decode(s["polyline"])]
        base = len(geometry)
        spans = s.get("spans") or []
        for a in s.get("actions") or []:
            actions.append((base + int(a.get("offset", 0)), a))
        limits += _speed_limits(spans, base)
        incidents += _incidents(s, spans, base)
        geometry += pts
        traffic += _traffic(pts, spans)
        meters += s["summary"]["length"]
        seconds += s["summary"]["duration"]
        raw += [str(n.get("code") or n.get("title") or "") for n in s.get("notices", [])]
    info = _info(geometry, meters, seconds, "here", _friendly(raw), True, traffic)
    info.maneuvers = _maneuvers(geometry, actions)
    n = len(geometry)
    info.speed_limits = _remap_limits(limits, n)
    info.incidents = [_incident(_thin_index(off, n), inc) for off, inc in incidents]
    return info


def _speed_limits(spans: List[dict], base: int) -> list[tuple[int, int]]:
    """(section-global offset, mph) for every span whose posted limit differs from the previous one."""
    out: list[tuple[int, int]] = []
    for sp in spans:
        ms = sp.get("maxSpeed")
        if not isinstance(ms, (int, float)) or ms <= 0:  # missing, or "unlimited"
            continue
        mph = int(round(ms * MPH_PER_MS))
        if out and out[-1][1] == mph:
            continue
        out.append((base + int(sp.get("offset", 0)), mph))
    return out


def _remap_limits(limits: list[tuple[int, int]], n: int) -> List[SpeedLimit]:
    """Offsets -> thinned indices; one entry per index (the first), consecutive equal limits merged."""
    out: List[SpeedLimit] = []
    for off, mph in limits:
        idx = _thin_index(off, n)
        if out and (out[-1].index == idx or out[-1].mph == mph):
            continue
        out.append(SpeedLimit(index=idx, mph=mph))
    return out


def _incidents(section: dict, spans: List[dict], base: int) -> list[tuple[int, dict]]:
    """(section-global offset, incident) for each incident of a section.

    HERE lists incidents per section and references them from spans by list position; the first
    span that references an incident gives its start. An incident no span mentions starts at the
    section start.
    """
    items = section.get("incidents")
    if not isinstance(items, list):
        return []
    first_offset: dict[int, int] = {}
    for sp in spans:
        for i in sp.get("incidents") or []:
            if isinstance(i, int) and i not in first_offset:
                first_offset[i] = base + int(sp.get("offset", 0))
    out = []
    for i, inc in enumerate(items):
        if isinstance(inc, dict):
            out.append((first_offset.get(i, base), inc))
    return out


def _incident(index: int, inc: dict) -> Incident:
    kind = str(inc.get("type") or "other")
    crit = str(inc.get("criticality") or "minor")
    text = re.sub(r"\s+", " ", str(inc.get("description") or kind)).strip().rstrip(".")
    if len(text) > INCIDENT_TEXT_MAX:
        text = text[:INCIDENT_TEXT_MAX].rsplit(" ", 1)[0]
    return Incident(type=kind, closed=kind == "roadClosure" or crit == "critical", description=text,
                    index=index, criticality=crit)


def _here_parse(data: dict) -> RouteInfo:
    """HERE Routing v8 response body -> the primary route (pure; no I/O)."""
    routes = (data or {}).get("routes") or []
    if not routes or not routes[0].get("sections"):
        raise RuntimeError("HERE returned no truck route")
    return _parse_route(routes[0])


async def _here(fl: float, fg: float, tl: float, tg: float, t: TruckProfile) -> RouteInfo:
    params = _here_params(t, (fl, fg), (tl, tg)) + [("apiKey", os.environ["HERE_API_KEY"])]
    r = await client().get(HERE_URL, params=params, timeout=20)
    if r.is_error:
        raise RuntimeError(f"HERE {r.status_code}: {r.text[:200]}")
    return _here_parse(r.json())


_GO_FOR = re.compile(r"\s+Go for .*$")


def _maneuvers(geometry: List[List[float]], actions: list) -> List[Maneuver]:
    """HERE actions -> maneuvers whose index points into the thinned geometry."""
    n = len(geometry)
    out = []
    for off, a in actions:
        if a.get("action") == "depart":
            continue
        off = min(off, n - 1)
        text = _GO_FOR.sub("", str(a.get("instruction") or "")).rstrip(".").strip()
        if not text:
            continue
        out.append(Maneuver(action=str(a.get("action")), direction=a.get("direction"), instruction=text,
                            lat=geometry[off][0], lng=geometry[off][1], index=_thin_index(off, n)))
    return out


def _friendly(codes: List[str]) -> List[str]:
    """HERE notice codes -> one short, deduplicated line a driver can act on."""
    out = []
    for c in codes:
        lc = c.lower()
        if lc == "tollroad" or "toll" in lc:
            msg = "Tolls couldn't be fully avoided on this route."
        elif "restriction" in lc or "violated" in lc:
            msg = "Part of the route may be restricted for your truck. Watch posted signs."
        elif "ferry" in lc:
            msg = "Route includes a ferry."
        elif lc:
            msg = "Check the route for restrictions."
        else:
            continue
        if msg not in out:
            out.append(msg)
    return out


async def _ors(fl: float, fg: float, tl: float, tg: float, t: TruckProfile) -> RouteInfo:
    body = {
        "coordinates": [[fg, fl], [tg, tl]],  # ORS uses [lng, lat]
        "instructions": False,
        "options": {
            "vehicle_type": "hgv",
            "profile_params": {"restrictions": {
                "height": round(t.height_in * 0.0254, 2),  # m
                "length": round(t.total_length_ft * 0.3048, 2),  # m
                "weight": round(t.weight_lb * 0.0004536, 1),  # metric tons
                "hazmat": t.hazmat,
            }},
        },
    }
    avoid = (["tollways"] if t.avoid_tolls else []) + (["ferries"] if t.avoid_ferries_tunnels else [])
    if avoid:
        body["options"]["avoid_features"] = avoid
    headers = {"Authorization": os.environ["ORS_API_KEY"], "Content-Type": "application/json"}
    r = await client().post(ORS_URL, headers=headers, json=body, timeout=25)
    if r.is_error:
        raise RuntimeError(f"ORS {r.status_code}: {r.text[:200]}")
    feats = r.json().get("features") or []
    if not feats:
        raise RuntimeError("ORS returned no route")
    coords = feats[0]["geometry"]["coordinates"]
    summary = feats[0].get("properties", {}).get("summary", {})
    geometry = [[round(c[1], 6), round(c[0], 6)] for c in coords]
    return _info(geometry, summary.get("distance", 0.0), summary.get("duration", 0.0), "openrouteservice", [], True)


def _mock(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> RouteInfo:
    # Quadratic bezier with the control point offset perpendicular to the chord.
    mid_lat, mid_lng = (from_lat + to_lat) / 2, (from_lng + to_lng) / 2
    d_lat, d_lng = to_lat - from_lat, to_lng - from_lng
    ctrl_lat, ctrl_lng = mid_lat - d_lng * 0.18, mid_lng + d_lat * 0.18
    geometry = []
    for i in range(POINTS + 1):
        t = i / POINTS
        lat = (1 - t) ** 2 * from_lat + 2 * (1 - t) * t * ctrl_lat + t ** 2 * to_lat
        lng = (1 - t) ** 2 * from_lng + 2 * (1 - t) * t * ctrl_lng + t ** 2 * to_lng
        wobble = math.sin(t * math.pi * 3) * math.sin(t * math.pi) * 0.0025
        geometry.append([round(lat + wobble, 6), round(lng - wobble, 6)])
    km = haversine_km(from_lat, from_lng, to_lat, to_lng) * ROAD_FACTOR
    mi = km_to_mi(km)
    minutes = max(1, round(mi / AVG_SPEED_MPH * 60))
    return _info(geometry, km * 1000, minutes * 60, "mock", [], False)


class RoutingUnavailable(RuntimeError):
    """Every configured routing provider failed (timeout, quota, bad response)."""


class TruckRoutingProvider:
    @property
    def source(self) -> str:
        if os.environ.get("HERE_API_KEY"):
            return "here"
        return "openrouteservice" if os.environ.get("ORS_API_KEY") else "mock"

    async def route(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float,
                    truck: Optional[TruckProfile] = None) -> RouteInfo:
        t = truck or TruckProfile()
        failed: list[str] = []
        for key, fn in (("HERE_API_KEY", _here), ("ORS_API_KEY", _ors)):
            if not os.environ.get(key):
                continue
            try:
                info = await fn(from_lat, from_lng, to_lat, to_lng, t)
                info.approach = from_route(info)
                return info
            except Exception as e:  # noqa: BLE001 - fall through to the next provider
                logger.warning("%s routing failed: %s", fn.__name__, e)
                failed.append(f"{fn.__name__}: {type(e).__name__}")
        if failed:  # a real provider is configured: never hand the driver a made-up line
            raise RoutingUnavailable("; ".join(failed))
        info = _mock(from_lat, from_lng, to_lat, to_lng)
        info.approach = from_route(info)
        return info


_provider = TruckRoutingProvider()


def get_routing_provider() -> TruckRoutingProvider:
    return _provider
