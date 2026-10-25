"""Truck routing.

Order: HERE Routing v8 (transportMode=truck) when HERE_API_KEY is set, then
OpenRouteService driving-hgv when ORS_API_KEY is set, then a MOCK curved line.
The truck profile (height, length, weight, hazmat) is sent to the real providers so
the route avoids low bridges and weight/length/hazmat restrictions.
"""
import logging
import math
import os
from typing import List, Optional

import flexpolyline
import httpx

from models import RouteInfo, TrafficSegment, TruckProfile
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
    for i, sp in enumerate(spans):
        level = _traffic_level(sp)
        if not level:
            continue
        start = sp["offset"]
        end = spans[i + 1]["offset"] if i + 1 < len(spans) else len(points) - 1
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


async def _here(fl: float, fg: float, tl: float, tg: float, t: TruckProfile) -> RouteInfo:
    params = {
        "transportMode": "truck",
        "origin": f"{fl},{fg}",
        "destination": f"{tl},{tg}",
        "return": "polyline,summary",
        "spans": "dynamicSpeedInfo",
        "vehicle[height]": round(t.height_in * 2.54),  # cm
        "vehicle[length]": round(t.total_length_ft * 30.48),  # cm
        "vehicle[grossWeight]": round(t.weight_lb * 0.4536),  # kg
        "apiKey": os.environ["HERE_API_KEY"],
    }
    if t.hazmat:
        params["vehicle[shippedHazardousGoods]"] = HERE_HAZMAT
    avoid = (["tollRoad"] if t.avoid_tolls else []) + (["ferry", "tunnel"] if t.avoid_ferries_tunnels else [])
    if avoid:
        params["avoid[features]"] = ",".join(avoid)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(HERE_URL, params=params)
    if r.is_error:
        raise RuntimeError(f"HERE {r.status_code}: {r.text[:200]}")
    routes = r.json().get("routes") or []
    if not routes or not routes[0].get("sections"):
        raise RuntimeError("HERE returned no truck route")
    geometry, meters, seconds, raw, traffic = [], 0.0, 0.0, [], []
    for s in routes[0]["sections"]:
        pts = [[round(p[0], 6), round(p[1], 6)] for p in flexpolyline.decode(s["polyline"])]
        geometry += pts
        traffic += _traffic(pts, s.get("spans") or [])
        meters += s["summary"]["length"]
        seconds += s["summary"]["duration"]
        raw += [str(n.get("code") or n.get("title") or "") for n in s.get("notices", [])]
    return _info(geometry, meters, seconds, "here", _friendly(raw), True, traffic)


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
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(ORS_URL, headers=headers, json=body)
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


class TruckRoutingProvider:
    @property
    def source(self) -> str:
        if os.environ.get("HERE_API_KEY"):
            return "here"
        return "openrouteservice" if os.environ.get("ORS_API_KEY") else "mock"

    async def route(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float,
                    truck: Optional[TruckProfile] = None) -> RouteInfo:
        t = truck or TruckProfile()
        for key, fn in (("HERE_API_KEY", _here), ("ORS_API_KEY", _ors)):
            if not os.environ.get(key):
                continue
            try:
                return await fn(from_lat, from_lng, to_lat, to_lng, t)
            except Exception as e:  # noqa: BLE001 - fall through to the next provider
                logger.warning("%s routing failed: %s", fn.__name__, e)
        return _mock(from_lat, from_lng, to_lat, to_lng)


_provider = TruckRoutingProvider()


def get_routing_provider() -> TruckRoutingProvider:
    return _provider
