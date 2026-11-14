"""Regional diesel baseline from the EIA weekly retail survey, so a price can be put in context:
"that's 30 cents above the regional average". Free API, weekly data, cached for a day.

EIA_API_KEY (free at eia.gov/opendata) and EIA_ENABLED=1 (default) turn it on. Any failure is
silent: the diesel sentence is simply spoken without the comparison.
"""
import logging
import os
import time
from typing import Optional

from services.flags import on
from services.http import client
from services.redact import describe

logger = logging.getLogger("truckbuddy.fuel_index")

EIA_URL = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"
REVGEOCODE_URL = "https://revgeocode.search.hereapi.com/v1/revgeocode"
PRODUCT = "EPD2D"  # No 2 Diesel, all types
TTL_S = 24 * 3600
NEAR_CENTS = 2  # up to this many cents off the average = "right at"
CELL_DEG = 0.5  # reverse-geocode cache cell

# EIA's weekly diesel (EPD2D) has one state series, California; every other state takes its PADD (sub)region.
# The gasoline survey has more state series (TX, NY, WA...), but not diesel: those duoareas return no rows, so
# mapping TX to STX left Texas without a price (checked live 2026-11-08).
_STATE_SERIES = {"CA": "SCA"}
_PADD = {
    "R1X": ("CT", "MA", "ME", "NH", "RI", "VT"),  # New England
    "R1Y": ("DE", "DC", "MD", "NJ", "NY", "PA"),  # Central Atlantic
    "R1Z": ("FL", "GA", "NC", "SC", "VA", "WV"),  # Lower Atlantic
    "R20": ("IL", "IN", "IA", "KS", "KY", "MI", "MN", "MO", "NE", "ND", "OH", "OK", "SD", "TN", "WI"),  # Midwest
    "R30": ("AL", "AR", "LA", "MS", "NM", "TX"),  # Gulf Coast
    "R40": ("CO", "ID", "MT", "UT", "WY"),  # Rocky Mountain
    "R50": ("AK", "AZ", "HI", "NV", "OR", "WA"),  # West Coast less California
}
STATE_TO_DUOAREA = {**{s: area for area, states in _PADD.items() for s in states}, **_STATE_SERIES}
REGION_NAME = {"SCA": "California", "R1X": "New England", "R1Y": "the Mid-Atlantic", "R1Z": "the Southeast",
               "R20": "the Midwest", "R30": "the Gulf Coast", "R40": "the Rockies", "R50": "the West Coast",
               "NUS": "the US"}

_avg_cache: dict[str, tuple[float, float, str]] = {}  # duoarea -> (fetched_at, price, period)
_state_cache: dict[tuple[int, int], tuple[float, Optional[str]]] = {}  # cell -> (fetched_at, state)


def parse_weekly(data: dict) -> Optional[tuple[float, str]]:
    """(price $/gal, period) from an EIA v2 response, newest row first; None when there is no usable row."""
    rows = ((data or {}).get("response") or {}).get("data") or []
    for row in rows:
        try:
            price = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        if price > 0:
            return round(price, 3), str(row.get("period") or "")
    return None


def compare(price: float, avg: float, region: str = "the region") -> str:
    """The spoken comparison. `region` is the human name of the baseline area."""
    cents = int(round((price - avg) * 100))
    name = region.removeprefix("the ")  # REGION_NAME has "the Midwest": "the the Midwest average" before
    where = "the regional average" if region == "the region" else f"the {name} average"
    if abs(cents) <= NEAR_CENTS:
        return f"That's right at {where}."
    if cents > 0:
        return f"That's {cents} cents above {where}."
    return f"That's {-cents} cents below {where}."


def _cell(lat: float, lng: float) -> tuple[int, int]:
    return int(lat // CELL_DEG), int(lng // CELL_DEG)


async def state_code(lat: float, lng: float, now: Optional[float] = None) -> Optional[str]:
    """US state code at a point via HERE reverse geocoding, cached per half-degree cell."""
    key = os.environ.get("HERE_API_KEY")
    if not key:
        return None
    now = time.monotonic() if now is None else now
    cell = _cell(lat, lng)
    hit = _state_cache.get(cell)
    if hit and now - hit[0] < TTL_S:
        return hit[1]
    try:
        r = await client().get(REVGEOCODE_URL, params={"at": f"{lat},{lng}", "lang": "en-US", "apiKey": key},
                               timeout=6)
        items = r.json().get("items") or [] if not r.is_error else []
        addr = (items[0].get("address") or {}) if items else {}
        state = addr.get("stateCode") if addr.get("countryCode") == "USA" else None
    except Exception as e:  # noqa: BLE001
        logger.warning("revgeocode failed: %s", describe(e))
        return None
    _state_cache[cell] = (now, state)
    return state


async def weekly_avg(duoarea: str, now: Optional[float] = None) -> Optional[tuple[float, str]]:
    """Latest weekly retail diesel average for an EIA area, cached for a day."""
    key = os.environ.get("EIA_API_KEY")
    if not key:
        return None
    now = time.monotonic() if now is None else now
    hit = _avg_cache.get(duoarea)
    if hit and now - hit[0] < TTL_S:
        return hit[1], hit[2]
    params = {"api_key": key, "frequency": "weekly", "data[0]": "value", "facets[product][]": PRODUCT,
              "facets[duoarea][]": duoarea, "sort[0][column]": "period", "sort[0][direction]": "desc", "length": "4"}
    try:
        r = await client().get(EIA_URL, params=params, timeout=8)
        if r.is_error:
            logger.warning("EIA %s: %s", r.status_code, r.text[:200])
            return None
        parsed = parse_weekly(r.json())
    except Exception as e:  # noqa: BLE001
        logger.warning("EIA failed: %s", describe(e))
        return None
    if not parsed:
        return None
    _avg_cache[duoarea] = (now, parsed[0], parsed[1])
    return parsed


ALL_AREAS = sorted({*STATE_TO_DUOAREA.values(), "NUS"})
REFRESH_S = 6 * 3600  # EIA publishes weekly; a few refreshes a day keep the cache from ever going cold


async def prewarm(now: Optional[float] = None) -> int:
    """Fetches the weekly average for every area in one EIA call, so no route ever waits for EIA.
    Returns how many areas were cached (0 without a key or on failure)."""
    key = os.environ.get("EIA_API_KEY")
    if not key or not on("EIA_ENABLED"):
        return 0
    now = time.monotonic() if now is None else now
    params: list[tuple[str, str]] = [("api_key", key), ("frequency", "weekly"), ("data[0]", "value"),
                                     ("facets[product][]", PRODUCT), ("sort[0][column]", "period"),
                                     ("sort[0][direction]", "desc"), ("length", str(len(ALL_AREAS) * 3))]
    params += [("facets[duoarea][]", a) for a in ALL_AREAS]
    try:
        r = await client().get(EIA_URL, params=params, timeout=15)
        if r.is_error:
            logger.warning("EIA prewarm %s: %s", r.status_code, r.text[:200])
            return 0
        rows = ((r.json() or {}).get("response") or {}).get("data") or []
    except Exception as e:  # noqa: BLE001
        logger.warning("EIA prewarm failed: %s", describe(e))
        return 0
    by_area: dict[str, list] = {}
    for row in rows:
        by_area.setdefault(str(row.get("duoarea") or ""), []).append(row)
    n = 0
    for area, area_rows in by_area.items():
        parsed = parse_weekly({"response": {"data": area_rows}})
        if area in ALL_AREAS and parsed:
            _avg_cache[area] = (now, parsed[0], parsed[1])
            n += 1
    logger.info("EIA prewarm: %d of %d areas", n, len(ALL_AREAS))
    return n


async def keep_warm() -> None:
    """Startup task: prewarm now and every REFRESH_S."""
    import asyncio
    while True:
        await prewarm()
        await asyncio.sleep(REFRESH_S)


async def regional_note(lat: float, lng: float, price: Optional[float]) -> str:
    """" That's 30 cents above the California average." for a diesel price at a point, or ''."""
    if not price or not on("EIA_ENABLED") or not os.environ.get("EIA_API_KEY"):
        return ""
    state = await state_code(lat, lng)
    area = STATE_TO_DUOAREA.get(state or "", "NUS")
    avg = await weekly_avg(area)
    if not avg:
        return ""
    return " " + compare(price, avg[0], REGION_NAME.get(area, "the region"))
