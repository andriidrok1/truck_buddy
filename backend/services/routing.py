"""Truck routing.

Order: HERE Routing v8 (transportMode=truck) when HERE_API_KEY is set, then
OpenRouteService driving-hgv when ORS_API_KEY is set. With no key configured at all the
route is a MOCK curved line (local demo); when a key is set and every provider fails,
route() raises RoutingUnavailable so the driver hears that routing is down instead of a
line that isn't a road.
The truck profile (height, length, weight, hazmat) is sent to the real providers so
the route avoids low bridges and weight/length/hazmat restrictions.
"""
import bisect
import json
import logging
import math
import os
import re
from datetime import datetime
from typing import List, Optional, Sequence

import flexpolyline
from services.http import client

from models import Incident, Maneuver, RouteInfo, SpeedLimit, TrafficSegment, TruckProfile
from services.approach import from_route
from services.errors import NoTruckRoute
from services.flags import on
from services.geo import haversine_km, km_to_mi
from services.speech_text import hours_minutes

logger = logging.getLogger("truckbuddy.routing")

ROAD_FACTOR = 1.3
AVG_SPEED_MPH = 32.0
POINTS = 32
MAX_POINTS = 6000  # hard cap on the points sent to the app; the tolerance below is doubled until the line fits
# The line the app gets stays within this of the road. A plain stride (every 10th point on a 1,000-mile trip)
# cut corners by 200 m and pulled maneuvers 800 m off their turns: the app spoke "turn now" early and
# called the truck off route on every bend (2026-11-01).
THIN_TOL_M = 5.0
HERE_URL = "https://router.hereapi.com/v8/routes"
ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-hgv/geojson"
HERE_HAZMAT = "explosive,gas,flammable,combustible,poison,corrosive"


def format_summary(duration_min: int, distance_mi: float) -> str:
    time = hours_minutes(duration_min)
    dist = f"{distance_mi:,.0f} mi" if distance_mi >= 100 else f"{distance_mi:.1f} mi"
    return f"{time} · {dist}"


def _simplify(points: Sequence[Sequence[float]], tol_m: float, keep: Sequence[int] = ()) -> List[int]:
    """Indices of the points to keep: Douglas-Peucker within `tol_m` metres, with the first, the last and every
    `keep` offset kept as they are (a maneuver, a limit change or an incident must stay an exact vertex)."""
    n = len(points)
    if n <= 2:
        return list(range(n))
    kept = bytearray(n)
    kept[0] = kept[-1] = 1
    for k in keep:
        if 0 <= k < n:
            kept[k] = 1
    # One local plane for the whole line: metres east/north of the first point. The scale error over a
    # 6-degree spread of latitude is a few percent of a 5 m tolerance.
    ky = 111_320.0
    kx = ky * math.cos(math.radians(sum(p[0] for p in points) / n))
    xs = [(p[1] - points[0][1]) * kx for p in points]
    ys = [(p[0] - points[0][0]) * ky for p in points]
    anchors = [i for i in range(n) if kept[i]]
    stack = list(zip(anchors, anchors[1:]))
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay, dx, dy = xs[a], ys[a], xs[b] - xs[a], ys[b] - ys[a]
        l2 = dx * dx + dy * dy
        far, at = 0.0, -1
        for i in range(a + 1, b):
            px, py = xs[i] - ax, ys[i] - ay
            if l2 == 0:
                d = math.hypot(px, py)
            else:
                t = min(1.0, max(0.0, (px * dx + py * dy) / l2))
                d = math.hypot(px - t * dx, py - t * dy)
            if d > far:
                far, at = d, i
        if far > tol_m:
            kept[at] = 1
            stack.append((a, at))
            stack.append((at, b))
    return [i for i in range(n) if kept[i]]


def _thin_indices(points: Sequence[Sequence[float]], keep: Sequence[int] = ()) -> List[int]:
    """_simplify at THIN_TOL_M, the tolerance doubled until the line fits MAX_POINTS (the anchors always stay)."""
    tol = THIN_TOL_M
    kept = _simplify(points, tol, keep)
    while len(kept) > MAX_POINTS and tol < 1000:
        tol *= 2
        kept = _simplify(points, tol, keep)
    return kept


def _thin(points: List[List[float]]) -> List[List[float]]:
    """The simplified line (no anchors): ORS and mock routes, traffic segments."""
    if len(points) <= 2:
        return points
    return [points[i] for i in _thin_indices(points)]


class _Index:
    """Original offset -> index into the thinned geometry: the kept vertex at or before the offset (exact for
    every anchor offset, since those are kept)."""

    def __init__(self, kept: Sequence[int]):
        self.kept = list(kept) or [0]

    def __call__(self, off: int) -> int:
        off = max(0, min(int(off), self.kept[-1]))
        return max(0, bisect.bisect_right(self.kept, off) - 1)


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
          truck_aware: bool, traffic: Optional[List[TrafficSegment]] = None,
          restricted: Optional[str] = None, thinned: bool = False) -> RouteInfo:
    mi = km_to_mi(meters / 1000)
    minutes = max(1, round(seconds / 60))
    return RouteInfo(geometry=geometry if thinned else _thin(geometry), distance_m=round(meters), duration_s=round(seconds),
                     distance_mi=round(mi, 1), duration_min=minutes, summary=format_summary(minutes, mi),
                     source=source, notices=notices, truck_aware=truck_aware, traffic=traffic or [],
                     restricted=restricted)


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
HERE_SPANS = "dynamicSpeedInfo,stateCode"  # stateCode: miles per state, for the diesel cost (route_cost)
MPH_PER_MS = 2.23694
MIN_LIMIT_MPH = 15  # below this a posted limit is believed only on a private road (a lot, a yard, a driveway)
INCIDENT_TEXT_MAX = 80
ALTERNATIVES = 2  # extra truck routes HERE is asked for (ROUTE_ALTERNATIVES=0 turns them off)


def default_alternatives() -> int:
    return ALTERNATIVES if on("ROUTE_ALTERNATIVES") else 0


Via = Sequence[Sequence[float]]  # [[lat, lng], ...]
VIA_STOP_S = 1800  # assumed time at each added stop, so the ETA stays honest


def _here_params(t: TruckProfile, origin: tuple[float, float], dest: tuple[float, float], *,
                 alternatives: int = 0, via: Via = ()) -> list[tuple[str, str]]:
    """Query for HERE Routing v8 as (key, value) pairs, so a key can repeat (via=...&via=...)."""
    ret, spans = [HERE_RETURN], [HERE_SPANS]
    if on("ROUTE_SPEED_LIMITS"):
        spans += ["maxSpeed", "streetAttributes"]  # streetAttributes: privateRoad keeps a 5 mph lot limit (_speed_limits)
    if on("ROUTE_INCIDENTS"):
        ret.append("incidents")
        spans.append("incidents")
    if on("ROUTE_ALTERNATIVES"):
        ret.append("routeLabels")
        ret.append("tolls")
    if on("REROUTE_ENABLED"):
        ret.append("routeHandle")
    params: list[tuple[str, str]] = [
        ("transportMode", "truck"),
        ("origin", f"{origin[0]},{origin[1]}"),
        ("destination", f"{dest[0]},{dest[1]}"),
        ("return", ",".join(ret)),
        ("lang", "en-US"),
        ("units", "imperial"),
        ("spans", ",".join(spans)),
        # Rounded up, never down: a 13 ft 6 in rig is 411.48 cm, and the router must plan for 412.
        ("vehicle[height]", str(math.ceil(t.height_in * 2.54))),  # cm
        ("vehicle[length]", str(math.ceil(t.total_length_ft * 30.48))),  # cm
        ("vehicle[grossWeight]", str(math.ceil(t.weight_lb * 0.45359237))),  # kg
        # A tractor with one trailer: HERE then applies the "no trailers" and trailer-length rules too,
        # which a plain straight truck of the same length would sail past.
        ("vehicle[type]", "tractor"),
        ("vehicle[trailerCount]", "1"),
    ]
    for stop in via:
        params.append(("via", f"{stop[0]},{stop[1]}!stopDuration={VIA_STOP_S}"))
    if on("ROUTE_ALTERNATIVES"):
        params.append(("currency", "USD"))
    if alternatives > 0:
        params.append(("alternatives", str(min(alternatives, 6))))
    if t.hazmat:
        params.append(("vehicle[shippedHazardousGoods]", HERE_HAZMAT))
    avoid = (["tollRoad"] if t.avoid_tolls else []) + (["ferry", "tunnel"] if t.avoid_ferries_tunnels else [])
    if avoid:
        params.append(("avoid[features]", ",".join(avoid)))
    return params


def _labels(route: dict) -> List[str]:
    out: List[str] = []
    for lab in route.get("routeLabels") or []:
        name = (lab or {}).get("name") if isinstance(lab, dict) else None
        value = name.get("value") if isinstance(name, dict) else None
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return out


def _tolls_usd(sections: List[dict]) -> Optional[float]:
    """Sum over tolls of the cheapest USD fare; None when no section lists a USD toll."""
    total, seen = 0.0, False
    for s in sections:
        for toll in s.get("tolls") or []:
            prices = []
            for fare in (toll or {}).get("fares") or []:
                price = (fare or {}).get("price") or {}
                value = _num(price.get("value"))
                if price.get("currency") == "USD" and value is not None:
                    prices.append(value)
            if prices:
                total += min(prices)
                seen = True
    return round(total, 2) if seen else None


def _part(name: str, fn, default, raw=None):
    """One optional piece of a HERE route (notices, tolls, limits...). If it can't be read, the route goes out
    without it and the log keeps a sample of what HERE sent: a parse error is not "the router is down"."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        PARSE_DEGRADED[name] = PARSE_DEGRADED.get(name, 0) + 1
        sample = ""
        if raw is not None:
            try:
                sample = json.dumps(raw, default=str)[:400]
            except Exception:  # noqa: BLE001
                sample = repr(raw)[:400]
        logger.warning("here parse degraded: %s: %s: %s %s", name, type(e).__name__, e, sample)
        return default


PARSE_DEGRADED: dict[str, int] = {}  # part -> times it was dropped since start (in /health)


def _parse_route(route: dict, truck: Optional[TruckProfile] = None) -> RouteInfo:
    """One HERE route object -> RouteInfo (pure; no I/O). Sections are concatenated in order; `truck` only
    puts the driver's own numbers into a restriction line."""
    sections = route.get("sections") or []
    if not sections:
        raise RuntimeError("HERE route has no sections")
    geometry, meters, seconds, raw, traffic, actions = [], 0.0, 0.0, [], [], []
    limits: list[tuple[int, Optional[int]]] = []  # (global offset, mph; None = no posted limit known)
    incidents: list[tuple[int, dict]] = []  # (global offset, incident)
    bases: list[int] = []
    state_km: dict[str, float] = {}
    for s in sections:
        pts = [[round(p[0], 6), round(p[1], 6)] for p in flexpolyline.decode(s["polyline"])]
        base = len(geometry)
        bases.append(base)
        spans = s.get("spans") or []
        actions += _part("actions", lambda: [(base + int(a.get("offset", 0)), a) for a in s.get("actions") or []
                                             if isinstance(a, dict)], [])
        limits += _part("speed_limits", lambda: _speed_limits(spans, base, limits[-1][1] if limits else None), [])
        incidents += _part("incidents", lambda: _incidents(s, spans, base), [], s.get("incidents"))
        geometry += pts
        traffic += _part("traffic", lambda: _traffic(pts, spans), [])
        meters += s["summary"]["length"]
        seconds += s["summary"]["duration"]
        raw += [n for n in (s.get("notices") or []) if isinstance(n, dict)]
        _part("states", lambda: _add_state_miles(state_km, pts, spans), None)
    stops_s = _stops_s(sections)
    if not geometry:
        raise RuntimeError("HERE route has no geometry")
    notices, restricted = _part("notices", lambda: _notices(raw, truck or TruckProfile()),
                                (["Check the route for restrictions."] if raw else [], None), raw)
    n = len(geometry)
    anchors = [min(off, n - 1) for off, _ in actions] + [off for off, _ in limits] + [off for off, _ in incidents] + bases
    idx = _Index(_thin_indices(geometry, anchors))
    info = _info([geometry[i] for i in idx.kept], meters, seconds, "here", notices, True, traffic, restricted,
                 thinned=True)
    info.stops_s = stops_s
    info.maneuvers = _part("maneuvers", lambda: _maneuvers(geometry, actions, idx), [])
    info.speed_limits = _part("speed_limits", lambda: _remap_limits(limits, idx), [])
    info.incidents = _part("incidents", lambda: [_incident(idx(off), inc) for off, inc in incidents], [])
    info.labels = _part("labels", lambda: _labels(route), [], route.get("routeLabels"))
    info.tolls_usd = _part("tolls", lambda: _tolls_usd(sections), None, [x.get("tolls") for x in sections])
    info.section_starts = [idx(b) for b in bases]
    info.set_state_miles(state_km)
    handle = route.get("routeHandle")
    info.handle = handle if isinstance(handle, str) and handle else None
    return info


def _add_state_miles(acc: dict[str, float], pts: List[List[float]], spans: List[dict]) -> None:
    """Kilometres of one section per US state, from the HERE stateCode spans (route_cost prices each state)."""
    for start, end, sp in _span_ranges(spans, len(pts)):
        st = sp.get("stateCode")
        if not st:
            continue
        km = sum(haversine_km(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(start, min(end, len(pts) - 1)))
        acc[st] = acc.get(st, 0.0) + km


def _stops_s(sections: List[dict]) -> float:
    """Seconds the truck stands at the via stops: the gap between one section's arrival and the next one's
    departure. Informational only: HERE already adds a via's stopDuration to the `duration` of the section that
    ends there (checked live 2026-11-02: 910 s -> 2710 s with stopDuration=1800), so adding it again put
    30 extra minutes on the ETA per stop."""
    total = 0.0
    for a, b in zip(sections, sections[1:]):
        try:
            arrive = datetime.fromisoformat(str(a["arrival"]["time"]))
            depart = datetime.fromisoformat(str(b["departure"]["time"]))
            total += max(0.0, (depart - arrive).total_seconds())
        except (KeyError, TypeError, ValueError):
            continue
    return total


# Keys the HERE "route by handle" endpoint rejects or that make no sense for a re-route.
_REROUTE_DROP = {"destination", "alternatives", "currency"}
_REROUTE_RETURN_DROP = {"routeLabels", "tolls"}


def _reroute_params(t: TruckProfile, origin: tuple[float, float], heading: Optional[int],
                    section_index: int, traveled_m: float) -> list[tuple[str, str]]:
    """Query for GET /v8/routes/{handle}: same truck and return options, origin with course, rerouting hints."""
    out: list[tuple[str, str]] = []
    for k, v in _here_params(t, origin, origin, alternatives=0):
        if k in _REROUTE_DROP:
            continue
        if k == "return":
            v = ",".join(x for x in v.split(",") if x not in _REROUTE_RETURN_DROP)
        if k == "origin" and heading is not None:
            v = f"{v};course={int(heading)}"
        out.append((k, v))
    out += [("rerouting[mode]", "returnToRoute"),
            ("rerouting[lastTraveledSectionIndex]", str(max(0, int(section_index)))),
            ("rerouting[traveledDistanceOnLastSection]", str(max(0, int(traveled_m))))]
    return out


async def _here_by_handle(handle: str, lat: float, lng: float, heading: Optional[int], t: TruckProfile,
                          section_index: int, traveled_m: float) -> RouteInfo:
    params = _reroute_params(t, (lat, lng), heading, section_index, traveled_m)
    params.append(("apiKey", os.environ["HERE_API_KEY"]))
    r = await client().get(f"{HERE_URL}/{handle}", params=params, timeout=20)
    if r.is_error:
        raise RuntimeError(f"HERE handle {r.status_code}: {r.text[:200]}")
    return _here_parse(r.json(), t)


def _speed_limits(spans: List[dict], base: int, prev: Optional[int] = None) -> list[tuple[int, Optional[int]]]:
    """(section-global offset, mph) for every span whose posted limit differs from the previous one; None where
    HERE has no limit for the stretch, so the app shows nothing there instead of the freeway's 65 on the ramp.
    `prev` is the limit in force at the end of the previous section, so a stretch without one right after a
    via stop ends it too. A limit under MIN_LIMIT_MPH counts as unknown unless HERE marks the span a private road:
    HERE sent 2.2 m/s (5 mph) on a street in West Sacramento and the app warned "you're doing 23 in a 5"."""
    out: list[tuple[int, Optional[int]]] = []
    last: Optional[int] = prev
    for sp in spans:
        ms = _num(sp.get("maxSpeed"))
        if ms is None or ms <= 0:  # missing, or "unlimited"
            if last is not None:
                out.append((base + int(sp.get("offset", 0)), None))
                last = None
            continue
        mph: Optional[int] = int(round(ms * MPH_PER_MS))
        if mph < MIN_LIMIT_MPH and "privateRoad" not in (sp.get("streetAttributes") or []):
            mph = None  # QA 2026-11-05, M-5: not a limit to show or to warn about
        if mph == last:
            continue
        last = mph
        out.append((base + int(sp.get("offset", 0)), mph))
    return out


def _remap_limits(limits: list[tuple[int, Optional[int]]], idx: "_Index") -> List[SpeedLimit]:
    """Offsets -> thinned indices; one entry per index (the first), consecutive equal limits merged."""
    out: List[SpeedLimit] = []
    for off, mph in limits:
        i = idx(off)
        if out and (out[-1].index == i or out[-1].mph == mph):
            continue
        out.append(SpeedLimit(index=i, mph=mph))
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


def _here_parse(data: dict, truck: Optional[TruckProfile] = None) -> RouteInfo:
    """HERE Routing v8 response body -> the primary route, with the other routes as .alternatives
    (pure; no I/O). An alternative HERE can't fully parse is dropped, never the primary."""
    routes = (data or {}).get("routes") or []
    if not routes or not routes[0].get("sections"):
        # A 200 with no route is HERE's answer, not a hiccup: "couldNotMatchOrigin", "noRouteFound"...
        why = "; ".join(str(n.get("title") or n.get("code") or "") for n in (data or {}).get("notices") or []
                        if isinstance(n, dict)) or "no truck route"
        raise NoTruckRoute(why)
    primary = _parse_route(routes[0], truck)
    for alt in routes[1:]:
        try:
            primary.alternatives.append(_parse_route(alt, truck))
        except Exception as e:  # noqa: BLE001
            logger.warning("HERE alternative skipped: %s", e)
    return primary


async def _here(fl: float, fg: float, tl: float, tg: float, t: TruckProfile, alternatives: int = 0,
                via: Via = ()) -> RouteInfo:
    params = _here_params(t, (fl, fg), (tl, tg), alternatives=alternatives, via=via)
    params.append(("apiKey", os.environ["HERE_API_KEY"]))
    r = await client().get(HERE_URL, params=params, timeout=20)
    if r.is_error:
        raise RuntimeError(f"HERE {r.status_code}: {r.text[:200]}")
    return _here_parse(r.json(), t)


_GO_FOR = re.compile(r"\s+Go for .*$")


def _maneuvers(geometry: List[List[float]], actions: list, idx: "_Index") -> List[Maneuver]:
    """HERE actions -> maneuvers whose index points into the thinned geometry (an exact vertex: every action
    offset is an anchor of the thinning)."""
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
                            lat=geometry[off][0], lng=geometry[off][1], index=idx(off)))
    return out


# HERE notice codes that are about the request, not the road (a re-route may start against the course,
# a polyline may be simplified): never a word to the driver.
_NOTICE_IGNORE = {"violatedstartdirection", "simplepolyline", "tollsdataunavailable", "mainlanguagenotfound",
                  "potentialtimedependentviolations", "nothroughrestrictions"}
RESTRICTED_LINE = {
    "height": "This route goes under a {limit} clearance; your truck is {truck}.",
    "weight": "This route crosses a {limit} weight limit; your truck is {truck}.",
    "length": "This route has a {limit} length limit; your truck is {truck}.",
    "width": "This route has a {limit} width limit.",
    "blocked": "Part of this route is on a road closed to trucks.",
    # HERE's `cause` is vendor text ("Violated access restriction for this type of vehicle"), spoken as is
    # (QA 2026-11-02): it goes to the log, the driver hears plain words.
    "other": "Part of this route is on a road with a truck restriction.",
}
LEGAL_LINE = "Your truck is over the legal {what} limit in {where} ({limit}); that takes a permit."


def _ft_in(cm: float) -> str:
    inches = int(cm / 2.54 + 1e-6)  # the limit rounded down: the conservative side for a clearance
    return _inches_text(inches)


def _inches_text(inches: int) -> str:
    return f"{inches // 12} ft {inches % 12} in" if inches % 12 else f"{inches // 12} ft"


def _num(x) -> Optional[float]:
    """A number from HERE however it is shaped: 3628, "3628" or {"value": 3628, "type": ...}. HERE changed
    maxWeight from a number to an object (2026-11) and every route with a weight restriction failed with
    "unsupported operand type(s) for /: 'dict' and 'float'"; every numeric HERE field is read through this."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, dict):
        return _num(x.get("value"))
    if isinstance(x, str):
        try:
            return float(x)
        except ValueError:
            return None
    return None


def _lb(kg: float) -> str:
    return f"{int(kg / 0.45359237):,} lb"


def _restriction(detail: dict) -> tuple[str, str]:
    """(kind, limit text) from a HERE restriction detail: maxHeight / maxWeight / maxLength / maxWidth in cm or kg."""
    for key, kind in (("maxHeight", "height"), ("maxWeight", "weight"), ("maxGrossWeight", "weight"),
                      ("maxLength", "length"), ("maxWidth", "width")):
        v = _num(detail.get(key))
        if v:
            return kind, (_lb(v) if kind == "weight" else _ft_in(v))
    return "other", ""


def _notices(raw: List[dict], t: TruckProfile) -> tuple[List[str], Optional[str]]:
    """HERE section notices -> (lines for the driver, the kind of truck restriction the route breaks or None).
    A violated vehicle restriction (critical: HERE found no compliant route and returned this one anyway) is
    named with its limit and comes first; the state's legal maximum (admin-wide, one line for all states and
    limits) says a permit is needed; avoid[] preferences that could not be honoured keep their one-liners;
    request-level codes are dropped."""
    physical: List[str] = []
    other: List[str] = []
    legal: dict[str, tuple[list[str], float]] = {}  # kind -> (states, highest limit)
    restricted: Optional[str] = None
    for n in raw:
        code = str(n.get("code") or "").lower()
        details = [d for d in (n.get("details") or []) if isinstance(d, dict)]
        if code in _NOTICE_IGNORE or not code:
            continue
        if code in ("violatedvehiclerestriction", "violatedtruckroadtype", "truckroadtype"):
            for d in details or [{}]:  # every detail: a notice can carry a height and a weight at once
                kind, limit = _restriction(d)
                cause = str(d.get("cause") or "").rstrip(".")
                if kind == "other" and cause:
                    logger.info("restriction without a known limit: %s", cause)
                if kind == "other" and code != "violatedvehiclerestriction":
                    msg = "Part of this route uses roads not approved for trucks."
                else:
                    truck = {"height": _inches_text(t.height_in), "weight": f"{t.weight_lb:,} lb",
                             "length": f"{t.total_length_ft} ft"}.get(kind, "")
                    msg = RESTRICTED_LINE[kind].format(limit=limit, truck=truck, cause=cause or "see the signs")
                restricted = restricted if restricted and restricted != "legal" else kind
                if msg not in physical:
                    physical.append(msg)
        elif code == "violatedblockedroad":
            restricted = restricted if restricted and restricted != "legal" else "blocked"
            if RESTRICTED_LINE["blocked"] not in physical:
                physical.append(RESTRICTED_LINE["blocked"])
        elif code == "violatedadminwidevehiclerestriction":
            d = details[0] if details else {}
            kind, _ = _restriction(d)
            value = next((v for v in (_num(d.get(k)) for k in ("maxHeight", "maxWeight", "maxGrossWeight", "maxLength",
                                                                 "maxWidth")) if v), 0.0)
            states, top = legal.get(kind, ([], 0.0))
            state = str(d.get("state") or d.get("country") or "").strip()
            if state and state not in states:
                states.append(state)
            legal[kind] = (states, max(top, value))
            restricted = restricted or "legal"
        elif code.startswith("violatedavoidtoll"):  # tollTransponder (info) is on almost every NJ/NY route
            msg = "Tolls couldn't be fully avoided on this route."
            if msg not in other:
                other.append(msg)
        elif "ferry" in code:
            msg = "Route includes a ferry."
            if msg not in other:
                other.append(msg)
        elif "tunnel" in code:  # violatedAvoidTunnel: the driver asked to avoid tunnels and there is no way around one
            msg = "A tunnel couldn't be avoided on this route."
            if msg not in other:
                other.append(msg)
        elif code.startswith("violated") or str(n.get("severity") or "").lower() == "critical":
            msg = "Check the route for restrictions."
            if msg not in other:
                other.append(msg)
    legal_lines = []
    for kind, (states, top) in legal.items():
        what = kind if kind in ("height", "weight", "length", "width") else "size"
        limit = (_lb(top) if kind == "weight" else _ft_in(top)) if top else "the posted value"
        where = " and ".join(states) if states else "this state"
        legal_lines.append(LEGAL_LINE.format(what=what, where=where, limit=limit))
    return physical + legal_lines + other, restricted


NO_TURNS_NOTICE = "The truck router is down, so this is a backup route: no turn prompts, follow the line on the map."


async def _ors(fl: float, fg: float, tl: float, tg: float, t: TruckProfile, via: Via = ()) -> RouteInfo:
    body = {
        "coordinates": [[fg, fl], *[[v[1], v[0]] for v in via], [tg, tl]],  # ORS uses [lng, lat]
        "instructions": False,
        "options": {
            "vehicle_type": "hgv",
            "profile_params": {"restrictions": {  # rounded up, like HERE's
                "height": math.ceil(t.height_in * 2.54) / 100,  # m
                "length": math.ceil(t.total_length_ft * 30.48) / 100,  # m
                "weight": math.ceil(t.weight_lb * 0.45359237 / 100) / 10,  # metric tons
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
                    truck: Optional[TruckProfile] = None, alternatives: Optional[int] = None,
                    via: Via = ()) -> RouteInfo:
        """Truck route; `alternatives` = how many other routes to ask HERE for (default: the flag);
        `via` = stops on the way, [[lat, lng], ...], each with a 30-minute stop in the ETA."""
        t = truck or TruckProfile()
        n_alt = default_alternatives() if alternatives is None else max(0, alternatives)
        via = [[float(v[0]), float(v[1])] for v in via]
        failed: list[str] = []
        no_route: Optional[NoTruckRoute] = None
        for key, fn in (("HERE_API_KEY", _here), ("ORS_API_KEY", _ors)):
            if not os.environ.get(key):
                continue
            try:
                info = await fn(from_lat, from_lng, to_lat, to_lng, t, n_alt, via) if fn is _here \
                    else await fn(from_lat, from_lng, to_lat, to_lng, t, via)
                info.approach = from_route(info)
                info.via = via
                if fn is _ors and failed:  # HERE is down: a line to follow, but no turn prompts
                    info.notices.append(NO_TURNS_NOTICE)
                for alt in info.alternatives:
                    alt.approach = from_route(alt)
                    alt.via = via
                return info
            except NoTruckRoute as e:
                # HERE knows the truck's restrictions; ORS asked after its "no" found a way HERE had ruled
                # out and handed it over without turns (review 2026-11-02, bug 20). The "no" is final.
                logger.warning("%s found no truck route: %s", fn.__name__, e)
                no_route = e
                break
            except Exception as e:  # noqa: BLE001 - fall through to the next provider
                logger.warning("%s routing failed: %s", fn.__name__, e)
                failed.append(f"{fn.__name__}: {type(e).__name__}")
        if no_route is not None:  # "no route" beats "down": the driver should pick another place, not wait
            raise no_route
        if failed:  # a real provider is configured: never hand the driver a made-up line
            raise RoutingUnavailable("; ".join(failed))
        info = _mock(from_lat, from_lng, to_lat, to_lng)
        info.approach = from_route(info)
        return info

    async def reroute(self, handle: Optional[str], lat: float, lng: float, heading: Optional[int],
                      to_lat: float, to_lng: float, truck: Optional[TruckProfile] = None, *,
                      section_index: int = 0, traveled_m: float = 0.0, via: Via = ()) -> RouteInfo:
        """Back onto the route the driver left (HERE route handle), else a fresh route from here.

        The handle call keeps the old route's shape past the rejoin point and avoids a U-turn; HERE can
        expire a handle at any time, so any failure falls back to a normal route() with no alternatives.
        """
        t = truck or TruckProfile()
        if handle and os.environ.get("HERE_API_KEY") and on("REROUTE_ENABLED"):
            try:
                info = await _here_by_handle(handle, lat, lng, heading, t, section_index, traveled_m)
                info.approach = from_route(info)
                info.via = [[float(v[0]), float(v[1])] for v in via]  # the handle keeps them; echo for the app
                return info
            except Exception as e:  # noqa: BLE001 - expired/invalid handle, HERE hiccup: fall back
                logger.warning("re-route by handle failed, routing fresh: %s", e)
        # A via stop ends a section: on section k the first k stops are behind the truck. Routing fresh
        # through all of them sent the driver back to a stop already made.
        ahead = list(via)[max(0, int(section_index)):]
        info = await self.route(lat, lng, to_lat, to_lng, t, alternatives=0, via=ahead)
        info.alternatives = []  # a re-route swaps the line under the driver; no chooser mid-drive
        return info


_provider = TruckRoutingProvider()


def get_routing_provider() -> TruckRoutingProvider:
    return _provider
