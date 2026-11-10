import math
import re
from typing import Optional

EARTH_RADIUS_KM = 6371.0088
KM_PER_MILE = 1.609344


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def km_to_mi(km: float) -> float:
    return km / KM_PER_MILE


_COUNTRY_TAIL = re.compile(r",\s*(?:United States(?: of America)?|USA|US)\s*$", re.I)


def without_country(text: Optional[str]) -> Optional[str]:
    """'Reno, NV, United States' -> 'Reno, NV'; '2100 Garson Rd, Verdi, NV 89439, USA' -> '... NV 89439'. Every place
    is in the US: the tail only made the card longer, and in Russian it stayed English (QA 2026-11-09, bug 8).
    A bare 'United States' (HERE's address of a town, once the title is cut off it) becomes empty."""
    if not text:
        return text
    out = _COUNTRY_TAIL.sub("", text.strip())
    return "" if re.fullmatch(r"(?:United States(?: of America)?|USA|US)", out, re.I) else out
