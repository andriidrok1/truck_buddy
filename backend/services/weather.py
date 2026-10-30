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

from models import WeatherAlert
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
}
WARNING_ONE = "Weather warning: {event} ahead near {area}."
WARNING_MANY = "{event} and {n} more weather alerts along the route."
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


def _area(p: dict) -> str:
    area = str(p.get("areaDesc") or "").split(";")[0].strip()
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
                                     ends=p.get("ends") or p.get("expires"))
    return sorted(seen.values(), key=lambda a: a.index)


def speech(alerts: list[WeatherAlert]) -> str:
    if not alerts:
        return ""
    first = alerts[0]
    if len(alerts) == 1:
        return WARNING_ONE.format(event=first.event, area=first.area)
    return WARNING_MANY.format(event=first.event, n=len(alerts) - 1)


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
    direction = str(p.get("windDirection") or "").strip().lower()
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
