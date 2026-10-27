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

logger = logging.getLogger("truckbuddy.fuel_index")

EIA_URL = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"
REVGEOCODE_URL = "https://revgeocode.search.hereapi.com/v1/revgeocode"
PRODUCT = "EPD2D"  # No 2 Diesel, all types
TTL_S = 24 * 3600
NEAR_CENTS = 2  # up to this many cents off the average = "right at"
CELL_DEG = 0.5  # reverse-geocode cache cell

# EIA publishes state series for a few states and PADD (sub)regions for the rest.
_STATE_SERIES = {"CA": "SCA", "CO": "SCO", "FL": "SFL", "MA": "SMA", "MN": "SMN", "NY": "SNY", "OH": "SOH",
                 "TX": "STX", "WA": "SWA"}
_PADD = {
    "R1X": ("CT", "ME", "NH", "RI", "VT"),  # New England (MA has its own series)
    "R1Y": ("NJ", "PA", "DE", "DC", "MD"),  # Central Atlantic (NY has its own)
    "R1Z": ("GA", "NC", "SC", "VA", "WV"),  # Lower Atlantic (FL has its own)
    "R20": ("IL", "IN", "IA", "KS", "KY", "MI", "MO", "NE", "ND", "OK", "SD", "TN", "WI"),  # Midwest (MN, OH own)
    "R30": ("AL", "AR", "LA", "MS", "NM"),  # Gulf Coast (TX has its own)
    "R40": ("ID", "MT", "UT", "WY"),  # Rocky Mountain (CO has its own)
    "R50": ("AK", "AZ", "HI", "NV", "OR"),  # West Coast (CA, WA have their own)
}
STATE_TO_DUOAREA = {**{s: area for area, states in _PADD.items() for s in states}, **_STATE_SERIES}
REGION_NAME = {"SCA": "California", "SCO": "Colorado", "SFL": "Florida", "SMA": "Massachusetts", "SMN": "Minnesota",
               "SNY": "New York", "SOH": "Ohio", "STX": "Texas", "SWA": "Washington", "R1X": "New England",
               "R1Y": "the Mid-Atlantic", "R1Z": "the Southeast", "R20": "the Midwest", "R30": "the Gulf Coast",
               "R40": "the Rockies", "R50": "the West Coast", "NUS": "the US"}

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
    where = "the regional average" if region == "the region" else f"the {region} average"
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
        logger.warning("revgeocode failed: %s", e)
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
        logger.warning("EIA failed: %s", e)
        return None
    if not parsed:
        return None
    _avg_cache[duoarea] = (now, parsed[0], parsed[1])
    return parsed


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
