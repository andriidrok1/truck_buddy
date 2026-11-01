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
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from models import RouteInfo, WeatherAlert
from services.flags import on
from services.geo import haversine_km
from services.http import client

logger = logging.getLogger("truckbuddy.weather")

BASE = "https://api.weather.gov"
SAMPLE_KM = 40.0
MAX_SAMPLES = 30
CONCURRENCY = 4
TIMEOUT_S = 6
ALERT_TTL_S = 600
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
NO_DETOUR_CLEARS = "No truck route around it; it should clear by {till}."
SAME_TIME = " and takes about the same time"
HOURLY = "{place} {when}: {temp} degrees and {forecast}{wind}."
HOURLY_ALERT = " Heads up, there's a {event} in effect there."
NO_FORECAST = "I couldn't get a forecast for {place} right now."

_alert_cache: dict[tuple[float, float], tuple[float, list[dict]]] = {}
_points_cache: dict[tuple[float, float], tuple[float, dict]] = {}
_forecast_cache: dict[str, tuple[float, list[dict]]] = {}


def _headers() -> dict:
    return {"User-Agent": os.environ.get("NWS_USER_AGENT", "(TruckBuddy, support@truckbuddy.io)"),
            "Accept": "application/geo+json"}


def sample_points(geometry: list[list[float]], every_km: float = SAMPLE_KM,
                  max_samples: int = MAX_SAMPLES) -> list[tuple[int, float, float]]:
    """(geometry index, lat, lng) every `every_km` along the line, always including the first and last point."""
    if not geometry:
        return []
    out = [(0, geometry[0][0], geometry[0][1])]
    since = 0.0
    for i in range(1, len(geometry)):
        since += haversine_km(geometry[i - 1][0], geometry[i - 1][1], geometry[i][0], geometry[i][1])
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
    """Active truck-relevant alerts along the route, each with the first geometry index it covers."""
    if not on("WEATHER_ALERTS") or not geometry:
        return []
    now = time.monotonic() if now is None else now
    samples = sample_points(geometry)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(idx: int, lat: float, lng: float):
        async with sem:
            return idx, await _alerts_at(lat, lng, now)

    results = await asyncio.gather(*(one(*s) for s in samples), return_exceptions=True)
    seen: dict[str, WeatherAlert] = {}
    for res in results:
        if isinstance(res, BaseException):
            continue
        idx, alerts = res
        for p in alerts:
            aid = str(p.get("id") or p.get("@id") or f"{p.get('event')}|{p.get('areaDesc')}")
            if aid in seen:
                continue
            seen[aid] = WeatherAlert(id=aid, event=str(p["event"]), severity=str(p.get("severity") or "Unknown"),
                                     headline=str(p.get("headline") or p["event"])[:160], area=_area(p), index=idx,
                                     ends=p.get("ends") or p.get("expires"), onset=p.get("onset") or p.get("effective"))
    return sorted(seen.values(), key=lambda a: a.index)


def speech(alerts: list[WeatherAlert], route: Optional[RouteInfo] = None, now: Optional[datetime] = None) -> str:
    """"Weather warning: High Wind Warning near Laramie, you'd hit it around 4 pm, in effect till 6 pm." With no
    route to time it against, the plain "ahead near Laramie"."""
    if not alerts:
        return ""
    first = alerts[0]
    n, s = len(alerts) - 1, "" if len(alerts) == 2 else "s"
    hit = _hit_label(first, route, now) if route is not None else ""
    if not hit:
        if len(alerts) == 1:
            return WARNING_ONE.format(event=first.event, area=first.area)
        return WARNING_MANY.format(event=first.event, n=n, s=s)
    if len(alerts) == 1:
        return WARNING_ONE_TIMED.format(event=first.event, area=first.area, hit=hit, till=_till_label(first, now))
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
    before = sum(haversine_km(g[i - 1][0], g[i - 1][1], g[i][0], g[i][1]) for i in range(1, index + 1))
    total = before + sum(haversine_km(g[i - 1][0], g[i - 1][1], g[i][0], g[i][1]) for i in range(index + 1, len(g)))
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


def on_arrival(alerts: list[WeatherAlert], route: RouteInfo, now: Optional[datetime] = None) -> list[WeatherAlert]:
    """Alerts still in effect when the truck gets to them. A warning that ends at 3 pm is nothing to a truck
    arriving at 5."""
    now = _now(now)
    out = []
    for a in alerts:
        end = _iso(a.ends) if a.ends else None
        if end is not None and end.tzinfo is not None and eta_at(route, a.index, now) >= end:
            continue
        out.append(a)
    return out


def score(alerts: list[WeatherAlert]) -> int:
    return sum(SEVERITY_WEIGHT.get(a.severity, 2) for a in alerts)


def better_route(route: RouteInfo) -> Optional[RouteInfo]:
    """Among the alternatives (each with its own `weather`), the one that weighs least, the fastest on a tie;
    None when nothing beats the route the driver has."""
    if not route.weather or not route.alternatives:
        return None
    best = min(route.alternatives, key=lambda r: (score(r.weather), r.duration_min))
    return best if score(best.weather) < score(route.weather) else None


def _diff(alt: RouteInfo, route: RouteInfo) -> str:
    d = alt.duration_min - route.duration_min
    if abs(d) < 5:
        return SAME_TIME
    h, m = divmod(abs(int(d)), 60)
    said = f"{h} h {m} min" if h and m else (f"{h} h" if h else f"{m} min")
    return f", about {said} {'longer' if d > 0 else 'faster'}"


def detour_speech(route: RouteInfo, now: Optional[datetime] = None) -> str:
    """What to do about the weather on `route`: the alternative that misses it (or meets less of it), or an
    honest "no way around", with the clearing time when the alert has one."""
    if not route.weather:
        return ""
    alt = better_route(route)
    if alt is None:
        first = route.weather[0]
        end = _iso(first.ends) if first.ends else None
        if end is not None and end.tzinfo is not None:
            return NO_DETOUR_CLEARS.format(till=_clock(end, _now(now)))
        return NO_DETOUR
    diff = _diff(alt, route)
    via = " and ".join(alt.labels[:2])
    if not alt.weather:
        return DETOUR_CLEAR.format(via=via, diff=diff) if via else DETOUR_CLEAR_NO_ROAD.format(diff=diff)
    return DETOUR_LIGHTER.format(via=via or "the other road", event=alt.weather[0].event, diff=diff)


async def for_route(route: RouteInfo, now: Optional[datetime] = None, mono: Optional[float] = None) -> list[WeatherAlert]:
    """Alerts on the route and on each of its alternatives (attached as `.weather`, only those still in effect
    on arrival), the point cache shared across them. Returns the route's own list."""
    mono = time.monotonic() if mono is None else mono
    now = _now(now)
    routes = [route] + list(route.alternatives)
    results = await asyncio.gather(*(alerts_along(r.geometry, mono) for r in routes), return_exceptions=True)
    for r, res in zip(routes, results):
        r.weather = [] if isinstance(res, BaseException) else on_arrival(res, r, now)
    return route.weather


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
