"""Weather at points of the route at the time the truck gets there (Figma "Weather v2": chips on the map, the
"Weather on the way" list, the time slider).

The app samples its route (about every 40 km, each point with its ETA) and asks POST /api/weather/points.
For each point: NWS /points (the grid cell and the nearest town, cached a day) and the raw forecast grid
(/gridpoints/..., hourly series for temperature, gusts, visibility, snow, ice, rain, cached 30 min), read at
the ETA. Free, no key (api.weather.gov). Every failure drops that point, never the answer.

Ice is an estimate: NWS ice accumulation where the office forecasts it, else "near freezing and wet in the
last 12 h". The app shows it as "Ice risk", not as a fact. Whether an alert covers a point is left to the app:
it has the route's alerts with their index spans.
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field

from services import weather
from services.http import client

logger = logging.getLogger("truckbuddy.weather")

GRID_TTL_S = 1800
CONCURRENCY = 4
BUDGET_S = 8.0
MAX_POINTS = 16
GUST_EMPTY_MPH = 40  # crosswind gusts that can tip an empty trailer (our threshold, not an official one)
GUST_ANY_MPH = 55  # a problem for any truck
ICE_MAX_F = 35.0  # pavement can ice a few degrees above freezing air, bridges first
WET_LOOKBACK_H = 12
LOW_VIS_MI = 1.0

KM_H_TO_MPH = 0.621371
M_TO_MI = 1 / 1609.344
MM_TO_IN = 1 / 25.4

_grid_cache: dict[str, tuple[float, dict]] = {}


class PointRequest(BaseModel):
    index: int = Field(ge=0)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    eta: datetime  # when the truck gets there (RFC 3339 with offset)


class PointsRequest(BaseModel):
    points: list[PointRequest] = Field(max_length=MAX_POINTS)
    trailer_empty: Optional[bool] = None


class RoutePoint(BaseModel):
    index: int
    lat: float
    lng: float
    eta: datetime
    place: Optional[str] = None  # nearest town from NWS, "Truckee, CA"
    temp_f: Optional[int] = None
    gust_mph: Optional[int] = None
    wind_mph: Optional[int] = None
    vis_mi: Optional[float] = None
    snow_in: Optional[float] = None  # snowfall in the forecast period around the ETA
    ice_in: Optional[float] = None
    kind: str = "clear"  # ice | snow | wind | fog | rain | cloud | clear: the one icon the chip shows
    ice_risk: bool = False
    severity: str = "info"  # warn | info (the app adds "inside an NWS alert" on top)
    reasons: list[str] = Field(default_factory=list)  # why warn: ice | gusts | low_visibility


# ---- grid series -------------------------------------------------------------------------------------------

_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def parse_valid_time(s: str) -> Optional[tuple[datetime, datetime]]:
    """'2026-11-11T15:00:00+00:00/PT3H' -> (start, end)."""
    try:
        start_s, dur = s.split("/")
        start = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    m = _DUR.match(dur)
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    return start, start + timedelta(days=d, hours=h, minutes=mi)


def value_at(series: Optional[dict], at: datetime):
    """The series value whose period covers `at`, None when none does (or the series is missing)."""
    for v in (series or {}).get("values") or []:
        span = parse_valid_time(v.get("validTime", ""))
        if span and span[0] <= at < span[1]:
            return v.get("value")
    return None


def total_between(series: Optional[dict], start: datetime, end: datetime) -> float:
    """Sum of an amount series (precipitation mm) over periods overlapping [start, end), pro rata."""
    total = 0.0
    for v in (series or {}).get("values") or []:
        span, val = parse_valid_time(v.get("validTime", "")), v.get("value")
        if not span or not isinstance(val, (int, float)) or val <= 0:
            continue
        a, b = max(span[0], start), min(span[1], end)
        if b > a:
            total += val * (b - a).total_seconds() / (span[1] - span[0]).total_seconds()
    return total


def weather_words(series: Optional[dict], at: datetime) -> set[str]:
    """NWS weather types at `at`: {'snow', 'rain', 'fog', 'freezing_rain', ...}."""
    vals = value_at(series, at) or []
    return {str(w.get("weather")) for w in vals if isinstance(w, dict) and w.get("weather")}


def _num(v, k: float = 1.0, nd: Optional[int] = None):
    if not isinstance(v, (int, float)):
        return None
    x = v * k
    return round(x) if nd is None else round(x, nd)


def read_point(grid: dict, at: datetime, trailer_empty: Optional[bool]) -> dict:
    """The fields of a RoutePoint (minus place/index/eta) from grid properties at `at`."""
    temp_c = value_at(grid.get("temperature"), at)
    temp_f = None if temp_c is None else round(temp_c * 9 / 5 + 32)
    gust = _num(value_at(grid.get("windGust"), at), KM_H_TO_MPH)
    wind = _num(value_at(grid.get("windSpeed"), at), KM_H_TO_MPH)
    vis = _num(value_at(grid.get("visibility"), at), M_TO_MI, 1)
    snow = _num(value_at(grid.get("snowfallAmount"), at), MM_TO_IN, 1)
    ice = _num(value_at(grid.get("iceAccumulation"), at), MM_TO_IN, 2)
    words = weather_words(grid.get("weather"), at)
    wet_mm = total_between(grid.get("quantitativePrecipitation"), at - timedelta(hours=WET_LOOKBACK_H), at)
    sky = value_at(grid.get("skyCover"), at)

    snowing = bool(snow) or any("snow" in w for w in words)
    raining = any(w in ("rain", "rain_showers", "drizzle", "thunderstorms") for w in words)
    freezing = any("freezing" in w or w == "sleet" for w in words)
    foggy = any("fog" in w for w in words) or (vis is not None and vis < LOW_VIS_MI)
    ice_risk = bool(ice) or freezing or (temp_f is not None and temp_f <= ICE_MAX_F and (wet_mm > 0 or snowing or raining))
    gusty = gust is not None and (gust >= GUST_ANY_MPH or (trailer_empty is True and gust >= GUST_EMPTY_MPH))

    if ice_risk:
        kind = "ice"
    elif snowing:
        kind = "snow"
    elif gusty:
        kind = "wind"
    elif foggy:
        kind = "fog"
    elif raining or (wet_mm > 0 and value_at(grid.get("quantitativePrecipitation"), at)):
        kind = "rain"
    elif isinstance(sky, (int, float)) and sky >= 50:
        kind = "cloud"
    else:
        kind = "clear"
    reasons = [r for r, on in (("ice", ice_risk), ("gusts", gusty), ("low_visibility", vis is not None and vis < LOW_VIS_MI)) if on]
    return dict(temp_f=temp_f, gust_mph=gust, wind_mph=wind, vis_mi=vis, snow_in=snow, ice_in=ice, kind=kind,
                ice_risk=ice_risk, severity="warn" if reasons else "info", reasons=reasons)


# ---- NWS -------------------------------------------------------------------------------------------------

async def _grid(url: str, now: float) -> Optional[dict]:
    hit = _grid_cache.get(url)
    if hit and now - hit[0] < GRID_TTL_S:
        return hit[1]
    r = await client().get(url, headers=weather._headers(), timeout=weather.TIMEOUT_S)
    if r.is_error:
        return None
    props = (r.json() or {}).get("properties") or {}
    if not isinstance(props, dict) or "temperature" not in props:
        return None
    if len(_grid_cache) > 2000:
        _grid_cache.clear()
    _grid_cache[url] = (now, props)
    return props


def _town(props: dict) -> Optional[str]:
    rel = ((props.get("relativeLocation") or {}).get("properties") or {})
    city, state = rel.get("city"), rel.get("state")
    return f"{city}, {state}" if city and state else city or None


async def one_point(p: PointRequest, trailer_empty: Optional[bool], now: float) -> Optional[RoutePoint]:
    props = await weather._points(p.lat, p.lng, now)
    url = (props or {}).get("forecastGridData")
    if not url:
        return None
    grid = await _grid(url, now)
    if not grid:
        return None
    eta = p.eta if p.eta.tzinfo else p.eta.replace(tzinfo=timezone.utc)
    return RoutePoint(index=p.index, lat=p.lat, lng=p.lng, eta=eta, place=_town(props),
                      **read_point(grid, eta, trailer_empty))


async def points(req: PointsRequest, budget_s: float = BUDGET_S, now: Optional[float] = None) -> list[RoutePoint]:
    """Each requested point that NWS answered for within the budget, in route order."""
    now = time.monotonic() if now is None else now
    sem = asyncio.Semaphore(CONCURRENCY)
    out: list[RoutePoint] = []

    async def go(p: PointRequest):
        async with sem:
            try:
                rp = await one_point(p, req.trailer_empty, now)
            except Exception as e:  # noqa: BLE001
                logger.info("route weather point %s skipped: %s", p.index, type(e).__name__)
                return
            if rp:
                out.append(rp)

    tasks = [asyncio.ensure_future(go(p)) for p in req.points[:MAX_POINTS]]
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=budget_s)
        for t in pending:
            t.cancel()
        if pending:
            logger.info("route weather: %d of %d points out of time", len(pending), len(tasks))
    return sorted(out, key=lambda r: r.index)


def clear_caches() -> None:
    _grid_cache.clear()
