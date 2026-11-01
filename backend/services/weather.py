"""Weather along the route and at a point, from the National Weather Service API (free, no key).

- alerts_along(geometry): active NWS alerts that matter to a truck (wind, snow, ice, fog, dust, flood),
  sampled every ~40 km along the route, deduplicated, each tagged with the geometry index it was found at.
- hourly(lat, lng, at): the hourly forecast period covering a moment, for "weather in Reno at 3 pm".

NWS asks for a User-Agent with a contact (NWS_USER_AGENT) and throttles bursts, so calls run four at a
time with short timeouts and small caches. Every failure degrades to "no weather", never to an error.
WEATHER_ALERTS=0 turns the route alerts off.
"""
import asyncio
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from models import RouteInfo, WeatherAlert
from services.flags import on
from services.geo import haversine_km
from services.http import client

logger = logging.getLogger("truckbuddy.weather")

BASE = "https://api.weather.gov"
SAMPLE_KM = 20.0  # the closest two samples get; a mountain zone can be 30 km across, 40 km stepped over it
MAX_SAMPLES = 30  # a long route is sampled more coarsely so it stays within this many NWS calls
CONCURRENCY = 4
TIMEOUT_S = 6
ALERT_TTL_S = 600
FEED_TTL_S = 180  # the all-US active-alert feed (one call for every route and driver); a flash flood warning
# issued five minutes ago must reach the next route, and the refresh runs in the background anyway
FEED_STALE_MAX_S = 3 * FEED_TTL_S  # past this a stale feed is not served; the route falls back to point lookups
ZONE_CONCURRENCY = 8  # zone polygons are small static files; NWS serves them fast
ZONE_CACHE_MAX = 4000
POINTS_TTL_S = 24 * 3600
FORECAST_TTL_S = 1800
# NWS event names that change how a truck should drive. Advisories included on purpose: a wind
# advisory is a real problem for an empty 53-footer.
EVENTS = {
    "High Wind Warning", "High Wind Watch", "Wind Advisory", "Extreme Wind Warning",
    "Winter Storm Warning", "Winter Storm Watch", "Winter Weather Advisory", "Blizzard Warning",
    "Ice Storm Warning", "Snow Squall Warning", "Freezing Rain Advisory", "Freezing Fog Advisory",
    "Dense Fog Advisory", "Dust Storm Warning", "Blowing Dust Advisory", "Dust Advisory",
    "Flash Flood Warning", "Flood Warning", "Extreme Cold Warning", "Tornado Warning", "Severe Thunderstorm Warning",
    # Cyclones: a Gulf or East Coast run meets these every autumn; a watch is already a reason to replan.
    "Hurricane Warning", "Hurricane Watch", "Tropical Storm Warning", "Tropical Storm Watch", "Storm Surge Warning",
    "Hurricane Force Wind Warning", "Avalanche Warning", "Dense Smoke Advisory",
}
# NWS severity -> how much an alert weighs when two routes are compared.
SEVERITY_WEIGHT = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1}
WARNING_ONE = "Weather warning: {event} ahead near {area}."
WARNING_ONE_TIMED = "Weather warning: {event} near {area}, you'd hit it around {hit}{till}."
WARNING_MANY = "{event} and {n} more weather alert{s} along the route."
WARNING_MANY_TIMED = "{event} near {area} around {hit} and {n} more weather alert{s} along the route."
TILL = ", in effect till {till}"
DETOUR_CLEAR = "The way via {via} misses it{diff}. Say 'take the other way' if you want it."
DETOUR_CLEAR_NO_ROAD = "The other way misses it{diff}. Say 'take the other way' if you want it."
DETOUR_LIGHTER = "The way via {via} only runs into a {event}{diff}. Say 'take the other way' if you want it."
NO_DETOUR = "No truck route around it."
NO_OTHER_ROAD = "I don't have another truck route to try."  # the router offered no alternative at all: nothing was compared
NO_DETOUR_CLEARS = "No truck route around it; it should clear by {till}."
SAME_TIME = " and takes about the same time"
LATER = "Heads up, a {event} near {area} starts {onset}; at this pace you're through there by {hit}."
HOURLY = "{place} {when}: {temp} degrees and {forecast}{wind}."
HOURLY_ALERT = " Heads up, there's a {event} in effect there."
NO_FORECAST = "I couldn't get a forecast for {place} right now."

_alert_cache: dict[tuple[float, float], tuple[float, list[dict]]] = {}
_feed_cache: dict[str, object] = {}  # {"at": monotonic, "alerts": [feature dict, ...]}
_feed_refresh: Optional[asyncio.Task] = None
_zone_cache: dict[str, Optional[list["Ring"]]] = {}  # zone URL -> rings (None: the fetch failed, retried next time)
_zone_fail: dict[str, int] = {}  # zone URL -> failed fetches so far
ZONE_GIVE_UP = 3
Ring = list[tuple[float, float]]  # [(lng, lat), ...] closed or not
_points_cache: dict[tuple[float, float], tuple[float, dict]] = {}
_forecast_cache: dict[str, tuple[float, list[dict]]] = {}


def _headers() -> dict:
    return {"User-Agent": os.environ.get("NWS_USER_AGENT", "(TruckBuddy, support@truckbuddy.io)"),
            "Accept": "application/geo+json"}


def _steps_km(geometry: list[list[float]]) -> list[float]:
    return [haversine_km(geometry[i - 1][0], geometry[i - 1][1], geometry[i][0], geometry[i][1])
            for i in range(1, len(geometry))]


def sample_points(geometry: list[list[float]], every_km: Optional[float] = None,
                  max_samples: int = MAX_SAMPLES) -> list[tuple[int, float, float]]:
    """(geometry index, lat, lng) along the line, always including the first and last point. The step is
    `every_km` when given, else SAMPLE_KM widened just enough for the whole route to fit in `max_samples`."""
    if not geometry:
        return []
    steps = _steps_km(geometry)
    if every_km is None:
        every_km = max(SAMPLE_KM, sum(steps) / max(1, max_samples - 2))
    out = [(0, geometry[0][0], geometry[0][1])]
    since = 0.0
    for i in range(1, len(geometry)):
        since += steps[i - 1]
        if since >= every_km and len(out) < max_samples - 1:
            out.append((i, geometry[i][0], geometry[i][1]))
            since = 0.0
    last = len(geometry) - 1
    if out[-1][0] != last:
        out.append((last, geometry[last][0], geometry[last][1]))
    return out


def parse_alerts(data: dict) -> list[dict]:
    """Alert feature properties that matter to trucks, from an NWS GeoJSON body."""
    out = []
    for f in ((data or {}).get("features") or []):
        p = (f or {}).get("properties") if isinstance(f, dict) else None
        if not isinstance(p, dict) or p.get("event") not in EVENTS:
            continue
        if p.get("status") not in (None, "Actual") or p.get("messageType") == "Cancel":
            continue
        out.append(p)
    return out


async def _alerts_at(lat: float, lng: float, now: float) -> list[dict]:
    key = (round(lat, 2), round(lng, 2))
    hit = _alert_cache.get(key)
    if hit and now - hit[0] < ALERT_TTL_S:
        return hit[1]
    try:
        r = await client().get(f"{BASE}/alerts/active", params={"point": f"{lat:.4f},{lng:.4f}"},
                               headers=_headers(), timeout=TIMEOUT_S)
        if r.is_error:
            logger.info("NWS alerts %s at %s", r.status_code, key)
            return []
        alerts = parse_alerts(r.json())
    except Exception as e:  # noqa: BLE001
        logger.info("NWS alerts failed at %s: %s", key, e)
        return []
    _alert_cache[key] = (now, alerts)
    return alerts


_AREA_TAIL = re.compile(r"\s+(?:of\s+.+?\s+Count(?:y|ies)|above\s+\d[\d,]*\s*(?:ft|feet)|below\s+\d[\d,]*\s*(?:ft|feet)|including\s+.+)$", re.I)


def _area(p: dict) -> str:
    """The first NWS zone, trimmed for speech: "Sierra Nevada of Sierra-Nevada-Placer Counties Above 5000 ft"
    is "Sierra Nevada" to a driver."""
    area = str(p.get("areaDesc") or "").split(";")[0].strip()
    for _ in range(3):
        short = _AREA_TAIL.sub("", area).strip()
        if short == area or not short:
            break
        area = short
    return area[:60] if area else "the route"


async def alerts_along(geometry: list[list[float]], now: Optional[float] = None) -> list[WeatherAlert]:
    """Active truck-relevant alerts along the route, each with the first geometry index it covers.
    The feed path (one all-US alert feed, cached, plus the static zone polygons, every route vertex tested)
    is exact and costs nothing per route once warm; the point path (an NWS call every 20 km) is the fallback
    when the feed is down and nothing is cached."""
    if not on("WEATHER_ALERTS") or not geometry:
        return []
    now = time.monotonic() if now is None else now
    try:
        found, complete = await _alerts_along_feed(geometry, now)
    except Exception as e:  # noqa: BLE001
        logger.info("alert feed path failed, sampling points: %s", type(e).__name__)
        found, complete = [], False
    if complete:
        return found
    # Some alert could not be placed (feed down, or a zone polygon missing): the point path covers it, and
    # what the feed did place is kept.
    by_id = {a.id: a for a in found}
    for a in await _alerts_along_points(geometry, now):
        by_id.setdefault(a.id, a)
    return sorted(by_id.values(), key=lambda a: a.index)


# ---- the feed path: all active alerts once, zone polygons once, pure geometry per route ----------

def _rings(geometry: Optional[dict]) -> list[Ring]:
    """GeoJSON Polygon/MultiPolygon -> rings of (lng, lat). Holes stay in: even-odd counting handles them."""
    if not isinstance(geometry, dict):
        return []
    coords, kind = geometry.get("coordinates"), geometry.get("type")
    polys = [coords] if kind == "Polygon" else (coords if kind == "MultiPolygon" else [])
    out: list[Ring] = []
    for poly in polys or []:
        for ring in poly or []:
            pts = [(float(c[0]), float(c[1])) for c in ring if isinstance(c, (list, tuple)) and len(c) >= 2]
            if len(pts) >= 3:
                out.append(pts)
    return out


def _bbox(rings: list[Ring]) -> tuple[float, float, float, float]:
    xs = [x for r in rings for x, _ in r]
    ys = [y for r in rings for _, y in r]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_rings(lng: float, lat: float, rings: list[Ring]) -> bool:
    """Even-odd ray casting over every ring: inside an outer ring and outside its holes."""
    inside = False
    for ring in rings:
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > lat) != (yj > lat):
                x = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
                if lng < x:
                    inside = not inside
            j = i
    return inside


def span_inside(geometry: list[list[float]], rings: list[Ring]) -> tuple[int, int]:
    """(first, last) route vertex ([lat, lng]) inside the rings, (−1, −1) when none; the bbox keeps it cheap.
    The last one matters as much as the first: a storm that starts while the truck is still in the zone is
    a storm the truck meets."""
    if not rings:
        return -1, -1
    x0, y0, x1, y1 = _bbox(rings)
    first = last = -1
    for i, (lat, lng) in enumerate(geometry):
        if x0 <= lng <= x1 and y0 <= lat <= y1 and point_in_rings(lng, lat, rings):
            if first < 0:
                first = i
            last = i
    return first, last


def first_index_inside(geometry: list[list[float]], rings: list[Ring]) -> int:
    """The first route vertex inside the rings, −1 when none."""
    return span_inside(geometry, rings)[0]


async def _fetch_feed() -> list[dict]:
    r = await client().get(f"{BASE}/alerts/active", params={"status": "actual", "message_type": "alert,update"},
                           headers=_headers(), timeout=TIMEOUT_S * 2)
    if r.is_error:
        raise RuntimeError(f"NWS feed {r.status_code}")
    data = r.json()
    feats = []
    for f in ((data or {}).get("features") or []):
        if not isinstance(f, dict):
            continue
        p = f.get("properties")
        if parse_alerts({"features": [f]}):
            feats.append({"properties": p, "geometry": f.get("geometry")})
    return feats


async def _feed(now: float) -> list[dict]:
    """The truck-relevant active alerts, refreshed every FEED_TTL_S; a stale copy is served while a refresh
    runs in the background, up to FEED_STALE_MAX_S."""
    global _feed_refresh
    at = _feed_cache.get("at")
    age = (now - at) if isinstance(at, (int, float)) else None
    if age is not None and age < FEED_TTL_S:
        return _feed_cache["alerts"]  # type: ignore[return-value]
    if age is not None and age < FEED_STALE_MAX_S:
        if _feed_refresh is None or _feed_refresh.done():
            _feed_refresh = asyncio.ensure_future(refresh_feed())
        return _feed_cache["alerts"]  # type: ignore[return-value]
    alerts = await _fetch_feed()
    _feed_cache.update({"at": now, "alerts": alerts})
    return alerts


async def refresh_feed() -> None:
    try:
        alerts = await _fetch_feed()
        _feed_cache.update({"at": time.monotonic(), "alerts": alerts})
        await _zones_for(alerts)
    except Exception as e:  # noqa: BLE001
        logger.info("NWS feed refresh failed: %s", type(e).__name__)


async def _fetch_zone(url: str) -> Optional[list[Ring]]:
    try:
        r = await client().get(url, headers=_headers(), timeout=TIMEOUT_S)
        if r.is_error:
            logger.info("NWS zone %s -> %s", url, r.status_code)
            return None
        return _rings((r.json() or {}).get("geometry"))
    except Exception as e:  # noqa: BLE001
        logger.info("NWS zone %s failed: %s", url, type(e).__name__)
        return None


async def _zones_for(alerts: list[dict]) -> bool:
    """Make sure every zone polygon the alerts need is cached. False when one is still missing (that alert
    can't be placed; the caller then adds the point path for cover). A zone that keeps failing is given up
    after ZONE_GIVE_UP tries, so one dead zone file somewhere in the US doesn't degrade every route forever."""
    needed = []
    for a in alerts:
        if a.get("geometry"):
            continue  # the alert carries its own polygon
        for z in (a["properties"].get("affectedZones") or []):
            if isinstance(z, str) and z.startswith(BASE) and _zone_cache.get(z) is None and z not in needed:
                needed.append(z)
    if not needed:
        return True
    if len(_zone_cache) + len(needed) > ZONE_CACHE_MAX:
        _zone_cache.clear()
        _zone_fail.clear()
    sem = asyncio.Semaphore(ZONE_CONCURRENCY)

    async def one(url: str):
        async with sem:
            rings = await _fetch_zone(url)
            if rings is None:
                _zone_fail[url] = _zone_fail.get(url, 0) + 1
                if _zone_fail[url] >= ZONE_GIVE_UP:
                    logger.warning("NWS zone %s failed %d times; giving up on it", url, _zone_fail[url])
                    rings = []
            _zone_cache[url] = rings

    await asyncio.gather(*(one(u) for u in needed), return_exceptions=True)
    return all(_zone_cache.get(u) is not None for u in needed)


def _alert_rings(a: dict) -> list[Ring]:
    if a.get("geometry"):
        return _rings(a["geometry"])
    rings: list[Ring] = []
    for z in (a["properties"].get("affectedZones") or []):
        rings.extend(_zone_cache.get(z) or [])
    return rings


async def warm() -> None:
    """At start-up: the feed and its zone polygons, so the first route of the day pays nothing."""
    await refresh_feed()


async def _alerts_along_feed(geometry: list[list[float]], now: float) -> tuple[list[WeatherAlert], bool]:
    """Alerts whose polygon the route enters, with the first and last vertex inside, and whether every alert
    could be placed (False: some zone polygon is missing, so the point path must cover for it)."""
    alerts = await _feed(now)
    complete = await _zones_for(alerts)
    seen: dict[str, WeatherAlert] = {}
    for a in alerts:
        p = a["properties"]
        first, last = span_inside(geometry, _alert_rings(a))
        if first < 0:
            continue
        aid = str(p.get("id") or p.get("@id") or f"{p.get('event')}|{p.get('areaDesc')}")
        if aid not in seen:
            seen[aid] = _to_alert(p, first, last)
    return sorted(seen.values(), key=lambda a: a.index), complete


def _to_alert(p: dict, idx: int, idx_end: Optional[int] = None) -> WeatherAlert:
    """`ends` is the hazard's end only (NWS `expires` is the product's expiry; the next issuance carries on,
    so it must never make an alert "over")."""
    aid = str(p.get("id") or p.get("@id") or f"{p.get('event')}|{p.get('areaDesc')}")
    return WeatherAlert(id=aid, event=str(p["event"]), severity=str(p.get("severity") or "Unknown"),
                        headline=str(p.get("headline") or p["event"])[:160], area=_area(p), index=idx,
                        index_end=idx_end, ends=p.get("ends"), onset=p.get("onset") or p.get("effective"))


# ---- the point path: an NWS call per sample point (fallback) --------------------------------------

async def _alerts_along_points(geometry: list[list[float]], now: float) -> list[WeatherAlert]:
    samples = sample_points(geometry)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(idx: int, lat: float, lng: float):
        async with sem:
            return idx, await _alerts_at(lat, lng, now)

    results = await asyncio.gather(*(one(*s) for s in samples), return_exceptions=True)
    seen: dict[str, WeatherAlert] = {}
    for res in sorted((r for r in results if not isinstance(r, BaseException)), key=lambda r: r[0]):
        idx, alerts = res
        for p in alerts:
            aid = str(p.get("id") or p.get("@id") or f"{p.get('event')}|{p.get('areaDesc')}")
            if aid in seen:
                seen[aid].index_end = idx  # the last sample point still inside
                continue
            seen[aid] = _to_alert(p, idx, idx)
    return sorted(seen.values(), key=lambda a: a.index)


def speech(alerts: list[WeatherAlert], route: Optional[RouteInfo] = None, now: Optional[datetime] = None) -> str:
    """"Weather warning: High Wind Warning near Laramie, you'd hit it around 4 pm, in effect till 6 pm." With no
    route to time it against, the plain "ahead near Laramie"."""
    if not alerts:
        return ""
    first = alerts[0]
    distinct = len({(a.event, a.area) for a in alerts})  # five flood warnings for one county are one thing to hear
    n, s = distinct - 1, "" if distinct == 2 else "s"
    hit = _hit_label(first, route, now) if route is not None else ""
    if not hit:
        if distinct == 1:
            till = _till_label(latest_end(alerts), now) if route is not None else ""  # in it already: still say when it ends
            return WARNING_ONE.format(event=first.event, area=first.area)[:-1] + till + "."
        return WARNING_MANY.format(event=first.event, n=n, s=s)
    if distinct == 1:
        return WARNING_ONE_TIMED.format(event=first.event, area=first.area, hit=hit, till=_till_label(latest_end(alerts), now))
    return WARNING_MANY_TIMED.format(event=first.event, area=first.area, hit=hit, n=n, s=s)


# ---- timing against the route and the choice between routes ---------------------------------

def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now().astimezone()


def eta_at(route: RouteInfo, index: int, now: Optional[datetime] = None) -> datetime:
    """When the truck reaches geometry point `index`, by the share of the route's length before it."""
    g = route.geometry
    if len(g) < 2 or route.distance_m <= 0:
        return _now(now)
    index = max(0, min(index, len(g) - 1))
    steps = _steps_km(g)
    before, total = sum(steps[:index]), sum(steps)
    share = before / total if total > 0 else 0.0
    return _now(now) + timedelta(seconds=route.duration_s * share)


def _alert_tz(alert: WeatherAlert, now: datetime):
    """The alert's own zone (from its times), so "4 pm" is the driver's clock, not the server's."""
    for s in (alert.ends, alert.onset):
        dt = _iso(s) if s else None
        if dt is not None and dt.tzinfo is not None:
            return dt.tzinfo
    return now.tzinfo


def _clock(at: datetime, now: datetime) -> str:
    """"4 pm" / "4:30 pm" / "tomorrow 6 am" / "Thursday 6 am"."""
    h = at.strftime("%-I %p").lower() if at.minute == 0 else at.strftime("%-I:%M %p").lower()
    days = (at.date() - now.astimezone(at.tzinfo).date()).days
    if days == 0:
        return h
    if days == 1:
        return f"tomorrow {h}"
    return f"{at.strftime('%A')} {h}"


def _hit_label(alert: WeatherAlert, route: RouteInfo, now: Optional[datetime]) -> str:
    """"4 pm", rounded to the half hour; "" when the truck is in it already (then "ahead near" is the line)."""
    now = _now(now)
    eta = eta_at(route, alert.index, now)
    if eta - now < timedelta(minutes=15):
        return ""
    at = eta.astimezone(_alert_tz(alert, now))
    at = at.replace(minute=(at.minute // 30) * 30, second=0, microsecond=0)
    return _clock(at, now)


def _till_label(alert: WeatherAlert, now: Optional[datetime]) -> str:
    """", in effect till 6 pm" when the alert says when it ends; "" otherwise."""
    end = _iso(alert.ends) if alert.ends else None
    if end is None or end.tzinfo is None:
        return ""
    return TILL.format(till=_clock(end, _now(now)))


ONSET_SLACK = timedelta(hours=3)  # an alert starting more than this after the ETA is for someone else


def latest_end(alerts: list[WeatherAlert]) -> WeatherAlert:
    """The alert that ends last (an alert with no end counts as the latest): "it should clear by" must be
    the end of the whole thing, not of the first warning on the list (QA 2026-11-01, Phoenix-Flagstaff)."""
    def key(a: WeatherAlert):
        end = _iso(a.ends) if a.ends else None
        return (1, datetime.max.replace(tzinfo=timezone.utc)) if end is None or end.tzinfo is None else (0, end)
    return max(alerts, key=key)


def on_arrival(alerts: list[WeatherAlert], route: RouteInfo, now: Optional[datetime] = None) -> list[WeatherAlert]:
    """Alerts in effect when the truck gets to them. A warning that ends at 3 pm is nothing to a truck arriving
    at 5, and NWS lists tomorrow evening's storm as active today: one starting more than ONSET_SLACK after the
    ETA doesn't count as weather on the route (a stop or two can shift the ETA, hence the slack); see
    `starting_later` for the heads-up it still gets, because a driver who sleeps first meets it in the morning."""
    now = _now(now)
    return [a for a in alerts if _timing(a, route, now) == "now"]


def starting_later(alerts: list[WeatherAlert], route: RouteInfo, now: Optional[datetime] = None) -> list[WeatherAlert]:
    """Alerts on the route that begin more than ONSET_SLACK after the truck would be there."""
    now = _now(now)
    return [a for a in alerts if _timing(a, route, now) == "later"]


def exit_eta(route: RouteInfo, a: WeatherAlert, now: datetime) -> datetime:
    """When the truck leaves the alert's area: the ETA at its last vertex inside, or at the route's end when
    that isn't known (the conservative side)."""
    end_index = a.index_end if a.index_end is not None else len(route.geometry) - 1
    return eta_at(route, max(end_index, a.index), now)


def _timing(a: WeatherAlert, route: RouteInfo, now: datetime) -> str:
    """"over" (ends before the truck even enters the area), "later" (starts more than ONSET_SLACK after the
    truck has left it), "now" (in effect somewhere while the truck is inside)."""
    entry, exit_ = eta_at(route, a.index, now), exit_eta(route, a, now)
    end = _iso(a.ends) if a.ends else None
    if end is not None and end.tzinfo is not None and entry >= end:
        return "over"
    start = _iso(a.onset) if a.onset else None
    if start is not None and start.tzinfo is not None and start - exit_ > ONSET_SLACK:
        return "later"
    return "now"


def later_speech(route: RouteInfo, now: Optional[datetime] = None) -> str:
    """"Heads up, a Winter Storm Warning near Red Mountain Pass starts tomorrow 6 am; at this pace you're
    through there by 12:30 am." for the first alert that begins after the truck has passed."""
    if not route.weather_later:
        return ""
    now = _now(now)
    a = route.weather_later[0]
    start = _iso(a.onset).astimezone(_alert_tz(a, now))  # type: ignore[union-attr] - "later" implies an onset
    out = exit_eta(route, a, now).astimezone(_alert_tz(a, now))  # "through there": when the truck has left the area
    out = out.replace(minute=(out.minute // 30) * 30, second=0, microsecond=0)
    return LATER.format(event=a.event, area=a.area, onset=_clock(start, now), hit=_clock(out, now))


def score(alerts: list[WeatherAlert]) -> int:
    return sum(SEVERITY_WEIGHT.get(a.severity, 2) for a in alerts)


def better_route(route: RouteInfo) -> Optional[RouteInfo]:
    """Among the alternatives (each with its own `weather`), the one that weighs least, the fastest on a tie;
    None when nothing beats the route the driver has. A road with a closure on it is never the way around."""
    if not route.weather or not route.alternatives:
        return None
    open_roads = [r for r in route.alternatives if r.weather_known and not any(i.closed for i in r.incidents)]
    if not open_roads:
        return None
    best = min(open_roads, key=lambda r: (score(r.weather), r.duration_min))
    return best if score(best.weather) < score(route.weather) else None


def _diff(alt: RouteInfo, route: RouteInfo) -> str:
    d = alt.duration_min - route.duration_min
    if abs(d) < 5:
        return SAME_TIME
    h, m = divmod(abs(int(d)), 60)
    said = f"{h} h {m} min" if h and m else (f"{h} h" if h else f"{m} min")
    return f", about {said} {'longer' if d > 0 else 'faster'}"


def detour_speech(route: RouteInfo, now: Optional[datetime] = None, alts_known: bool = True,
                  said_till: bool = False) -> str:
    """What to do about the weather on `route`: the alternative that misses it (or meets less of it), or an
    honest "no way around", with the clearing time when the alert has one and the warning line hasn't
    just said it (`said_till`; "in effect till 3:45 pm. ... it should clear by 3:45 pm" was heard on the
    stand). With the alternatives' weather unknown (lookup cut off) nothing is claimed either way."""
    if not route.weather or not alts_known:
        return ""
    alt = better_route(route)
    if alt is None and not route.alternatives:
        return NO_OTHER_ROAD  # "around it" would claim a comparison that never happened (QA 2026-11-01)
    if alt is None:
        last = latest_end(route.weather)
        end = _iso(last.ends) if last.ends else None
        if end is not None and end.tzinfo is not None and not said_till:
            return NO_DETOUR_CLEARS.format(till=_clock(end, _now(now)))
        return NO_DETOUR
    diff = _diff(alt, route)
    via = " and ".join(alt.labels[:2])
    if not alt.weather:
        return DETOUR_CLEAR.format(via=via, diff=diff) if via else DETOUR_CLEAR_NO_ROAD.format(diff=diff)
    return DETOUR_LIGHTER.format(via=via or "the other road", event=alt.weather[0].event, diff=diff)


async def for_route(route: RouteInfo, budget_s: Optional[float] = None, alt_budget_s: Optional[float] = None,
                    now: Optional[datetime] = None, mono: Optional[float] = None) -> bool:
    """Alerts on the route and on each of its alternatives (attached as `.weather`, only those still in effect
    on arrival), the point cache shared across them. The route's own lookup gets `budget_s`; the alternatives
    run alongside and get `alt_budget_s` more, so a slow NWS costs the detour line, never the warning.
    Returns whether the alternatives' weather is known (False: nothing may be claimed about them)."""
    mono = time.monotonic() if mono is None else mono
    now = _now(now)
    alts = list(route.alternatives)
    for r in [route] + alts:
        r.weather, r.weather_known = [], False
    route.weather_later = []
    main = asyncio.ensure_future(alerts_along(route.geometry, mono))
    side = asyncio.ensure_future(asyncio.gather(*(alerts_along(a.geometry, mono) for a in alts), return_exceptions=True)) \
        if alts and (alt_budget_s is None or alt_budget_s > 0) else None  # 0 = don't look at the alternatives
    try:
        found = await (asyncio.wait_for(main, budget_s) if budget_s else main)
        route.weather, route.weather_later = on_arrival(found, route, now), starting_later(found, route, now)
        route.weather_known = True
    except Exception as e:  # noqa: BLE001 - incl. timeout
        logger.info("route weather skipped: %s", type(e).__name__)
        main.cancel()
    if side is None:
        return True
    try:
        results = await (asyncio.wait_for(side, alt_budget_s) if alt_budget_s else side)
    except Exception as e:  # noqa: BLE001
        logger.info("alternatives weather skipped: %s", type(e).__name__)
        side.cancel()
        return False
    for a, res in zip(alts, results):
        if not isinstance(res, BaseException):
            a.weather, a.weather_known = on_arrival(res, a, now), True
    return True


# ---- point forecast --------------------------------------------------------------------------

async def _points(lat: float, lng: float, now: float) -> Optional[dict]:
    key = (round(lat, 1), round(lng, 1))
    hit = _points_cache.get(key)
    if hit and now - hit[0] < POINTS_TTL_S:
        return hit[1]
    r = await client().get(f"{BASE}/points/{lat:.4f},{lng:.4f}", headers=_headers(), timeout=TIMEOUT_S)
    if r.is_error:
        return None
    props = (r.json() or {}).get("properties") or {}
    if not props.get("forecastHourly"):
        return None
    _points_cache[key] = (now, props)
    return props


async def _hourly_periods(url: str, now: float) -> list[dict]:
    hit = _forecast_cache.get(url)
    if hit and now - hit[0] < FORECAST_TTL_S:
        return hit[1]
    r = await client().get(url, headers=_headers(), timeout=TIMEOUT_S)
    if r.is_error:
        return []
    periods = ((r.json() or {}).get("properties") or {}).get("periods") or []
    periods = [p for p in periods if isinstance(p, dict) and p.get("startTime")]
    _forecast_cache[url] = (now, periods)
    return periods


def _iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def pick_period(periods: list[dict], at: datetime) -> Optional[dict]:
    """The hourly period covering `at` (tz-aware); the first period when `at` is before the forecast starts."""
    for p in periods:
        start, end = _iso(p.get("startTime", "")), _iso(p.get("endTime", ""))
        if start and end and start <= at < end:
            return p
    first = periods[0] if periods else None
    if first and (start := _iso(first.get("startTime", ""))) and at < start:
        return first
    return None


WHEN_RX = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", re.I)
# Longer words first: "midnight" contains "night", "afternoon" contains "noon".
PART_OF_DAY = {"midnight": 0, "afternoon": 15, "morning": 8, "noon": 12, "evening": 19, "tonight": 21, "night": 21}


def resolve_when(text: Optional[str], now: datetime) -> datetime:
    """'3pm', '15:30', 'tomorrow morning', 'tonight', None -> a tz-aware datetime in `now`'s zone."""
    t = (text or "").strip().lower()
    if not t or t in ("now", "right now", "currently"):
        return now
    day = now
    if "tomorrow" in t:
        day = now + timedelta(days=1)
    hour: Optional[int] = None
    minute = 0
    m = WHEN_RX.search(t)
    if m and (m.group(3) or m.group(2) or int(m.group(1)) <= 24):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = (m.group(3) or "").replace(".", "")
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif not ampm and hour <= 6 and "morning" not in t:  # "at 3" said while driving: afternoon
            hour += 12
    if hour is None:
        for word, h in PART_OF_DAY.items():
            if word in t:
                hour = h
                break
    if hour is None:
        return day if day != now else now
    at = day.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if "tomorrow" not in t and at < now - timedelta(hours=2):
        at += timedelta(days=1)  # "at 8" at 9 pm means tomorrow morning; "12 pm" at 1 pm still means today
    return at


_COMPASS = {"n": "north", "s": "south", "e": "east", "w": "west"}


def compass_words(abbr: str) -> str:
    """NWS gives the wind direction as "N", "SW", "NNE"; spoken that was "from the n" (QA 2026-10-31, H17).
    "N" -> "north", "SW" -> "southwest", "NNE" -> "north-northeast"; anything else is passed through lowercased."""
    a = abbr.strip().lower()
    if not a:
        return ""
    if all(c in _COMPASS for c in a) and 1 <= len(a) <= 3:
        if len(a) == 3:
            return f"{_COMPASS[a[0]]}-{_COMPASS[a[1]]}{_COMPASS[a[2]]}"
        return "".join(_COMPASS[c] for c in a)
    return a


def _wind(p: dict) -> str:
    """", wind 12 miles an hour from the southwest, gusting 25" / ", calm" / "" when NWS gave no wind.
    A direction is only spoken when there is one: a calm period has none, and "from the no direction"
    is what the driver used to hear."""
    raw = str(p.get("windSpeed") or "").strip()
    speeds = [int(x) for x in re.findall(r"\d+", raw)]
    if not speeds:
        return ""
    if max(speeds) == 0:
        return ", calm"
    out = ", wind " + raw.replace(" mph", " miles an hour")
    direction = compass_words(str(p.get("windDirection") or ""))
    if direction:
        out += f" from the {direction}"
    gust = p.get("windGust")
    if gust:
        out += f", gusting {str(gust).replace(' mph', '')}"
    return out


def _when_label(at: datetime, now: datetime) -> str:
    """"at 3 pm", "tomorrow at 3 pm", "Thursday at 3 pm": the day is said whenever it isn't today."""
    h = at.strftime("%-I %p").lower() if at.minute == 0 else at.strftime("%-I:%M %p").lower()
    days = (at.date() - now.date()).days
    if days == 0:
        return f"at {h}"
    if days == 1:
        return f"tomorrow at {h}"
    return f"{at.strftime('%A')} at {h}"


async def forecast_speech(place: str, lat: float, lng: float, when_text: Optional[str],
                          now: Optional[datetime] = None, mono: Optional[float] = None) -> str:
    """The spoken hourly forecast at a place and time, plus any truck-relevant alert in effect there."""
    mono = time.monotonic() if mono is None else mono
    try:
        props = await _points(lat, lng, mono)
        if not props:
            return NO_FORECAST.format(place=place)
        tz = ZoneInfo(props.get("timeZone") or "UTC")
        now = (now or datetime.now(tz)).astimezone(tz)
        at = resolve_when(when_text, now)
        periods = await _hourly_periods(props["forecastHourly"], mono)
        period = pick_period(periods, at)
        if not period:
            return NO_FORECAST.format(place=place)
        out = HOURLY.format(place=place, when=_when_label(at, now), temp=period.get("temperature", "?"),
                            forecast=str(period.get("shortForecast") or "no details").lower(), wind=_wind(period))
        alerts = await _alerts_at(lat, lng, mono)
        if alerts:
            out += HOURLY_ALERT.format(event=alerts[0]["event"])
        return out
    except Exception as e:  # noqa: BLE001
        logger.info("forecast failed for %s: %s", place, e)
        return NO_FORECAST.format(place=place)
