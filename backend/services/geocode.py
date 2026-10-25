"""Address / city / landmark geocoding for "take me to ..." requests (USA).

HERE (Geocode for addresses + Discover for businesses/landmarks) when HERE_API_KEY is set,
then OpenRouteService geocoder (ORS_API_KEY), then OpenStreetMap Nominatim (free, low volume).
Low-confidence matches are rejected so gibberish never becomes a 2,000 mile route.
"""
import asyncio
import logging
import os
import re
from typing import Optional

from services.http import client

from models import Place
from services.geo import haversine_km, km_to_mi
from services.google_places import PlacesUnavailable

logger = logging.getLogger("truckbuddy.geocode")

MIN_HERE_SCORE = 0.75
MIN_ADDRESS_SCORE = 0.8  # street / place / postal code / intersection / house number
ADDRESS_TYPES = ("houseNumber", "street", "place", "postalCode", "intersection")
AREA_TYPES = ("locality", "administrativeArea")  # towns need a near-perfect match (wrong-town risk)
MIN_ORS_CONFIDENCE = 0.7
FAR_KM = 500.0  # a weak match this far from the driver is almost always the wrong town
NOMINATIM_BOX_DEG = 3.0  # viewbox around the driver (preferred, not strict)
STOP = {"the", "of", "to", "and", "in", "at", "st", "street", "ave", "rd", "road", "near", "me", "usa", "us"}

_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_UNITS["oh"] = 0
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if i > 1}
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
        "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
        "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
        "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50}
_SUFFIX = lambda n: "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")  # noqa: E731
_NUM_WORD = re.compile(r"\b(?:" + "|".join(sorted([*_UNITS, *_TENS, *_ORD, "hundred", "thousand"], key=len,
                                                  reverse=True)) + r")\b(?:[\s-]+(?=\w))?", re.I)


def _run_value(words: list[str]) -> str:
    """One run of number words -> digits. With hundred/thousand: the value ("twelve hundred" -> 1200);
    otherwise spoken addresses glue chunks ("twenty eight twenty eight" -> 2828, "two eight" -> 28)."""
    ordinal = words[-1] in _ORD
    if any(w in ("hundred", "thousand") for w in words):
        total, cur = 0, 0
        for w in words:
            if w == "thousand":
                total, cur = total + max(cur, 1) * 1000, 0
            elif w == "hundred":
                cur = max(cur, 1) * 100
            else:
                cur += _UNITS.get(w, _TENS.get(w, _ORD.get(w, 0)))
        n = total + cur
        return f"{n}{_SUFFIX(n)}" if ordinal else str(n)
    chunks: list[int] = []
    pending_tens = False
    for w in words:
        v = _UNITS.get(w, _TENS.get(w, _ORD.get(w)))
        if pending_tens and v < 10 and w not in ("oh", "zero"):
            chunks[-1] += v
            pending_tens = False
            continue
        chunks.append(v)
        pending_tens = w in _TENS
    out = "".join(str(c) for c in chunks)
    return f"{out}{_SUFFIX(chunks[-1])}" if ordinal else out


def words_to_digits(q: str) -> str:
    """'twenty eight twenty eight El Centro Road' -> '2828 El Centro Road'; 'Interstate eighty' -> 'Interstate 80'."""
    out, i = [], 0
    q = re.sub(r"\b(hundred|thousand)\s+and\s+", r"\1 ", q, flags=re.I)  # "one hundred and ten" -> 110
    for m in re.finditer(r"(?:" + _NUM_WORD.pattern + r")+", q, re.I):
        words = [w.lower() for w in re.findall(r"[a-zA-Z]+", m.group(0))]
        if words == ["oh"] or words == ["one"] and not re.match(r"\s*(?:\d|[a-z]+\s+(?:st|street|rd|road|ave|avenue|dr|drive|way|blvd)\b)", q[m.end():], re.I):
            continue  # "oh", "the one on Main" stay words
        trail = " " if m.group(0)[-1].isspace() or m.group(0)[-1] == "-" else ""
        out.append(q[i:m.start()] + _run_value(words) + trail)
        i = m.end()
    out.append(q[i:])
    return "".join(out)


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1 and w not in STOP}


def _overlap(query: str, text: str) -> float:
    q = _words(query)
    return len(q & _words(text)) / len(q) if q else 0.0


def _place(pid: str, name: str, lat: float, lng: float, address: Optional[str], at: tuple[float, float]) -> Place:
    km = haversine_km(at[0], at[1], lat, lng)
    if address and address.lower().startswith(name.lower() + ", "):
        address = address[len(name) + 2:]  # Discover labels repeat the title
    if address and address.strip().lower() == name.strip().lower():
        address = None  # don't show the same text twice
    return Place(id=pid, name=name, category="destination", lat=lat, lng=lng, address=address,
                 distance_km=round(km, 2), distance_mi=round(km_to_mi(km), 1))


def _here_item(it: dict, at: tuple[float, float]) -> Optional[Place]:
    pos = (it.get("access") or [None])[0] or it.get("position")  # access point = where a vehicle can enter
    if not pos:
        return None
    addr = (it.get("address") or {}).get("label")
    town = it.get("resultType") in AREA_TYPES  # a whole town: no satellite parking check
    return _place(f"here-{'town-' if town else ''}{it.get('id')}", it.get("title") or addr or "Destination",
                  pos["lat"], pos["lng"], addr, at)


async def _here(q: str, lat: float, lng: float) -> list[Place]:
    found = await _here_once(q, lat, lng)
    if found:
        return found
    # "Pilot, 3740 Ramos Rd, Williams": the business name confuses the address search.
    # Retry with the address part, then with just the town (both anchored at the driver).
    parts = [p.strip() for p in q.split(",") if p.strip()]
    for alt in ([", ".join(parts[1:])] if len(parts) > 1 and not re.search(r"\d", parts[0]) else []) + \
               ([parts[-1]] if len(parts) > 1 else []):
        found = await _here_once(alt, lat, lng)
        if found:
            return found
    return []


def _near_enough(p: Place, strong: bool) -> bool:
    return strong or (p.distance_km or 0) <= FAR_KM


async def _here_once(q: str, lat: float, lng: float) -> list[Place]:
    key = os.environ["HERE_API_KEY"]
    common = {"q": q, "at": f"{lat},{lng}", "limit": 3, "apiKey": key, "in": "countryCode:USA"}
    c = client()
    geo, disc = await asyncio.gather(
        c.get("https://geocode.search.hereapi.com/v1/geocode", params=common, timeout=10),
        c.get("https://discover.search.hereapi.com/v1/discover", params=common, timeout=10),
    )
    geo.raise_for_status()
    items = geo.json().get("items", [])
    top = items[0] if items else None
    score = ((top or {}).get("scoring") or {}).get("queryScore", 0)
    rtype = (top or {}).get("resultType")
    logger.info("here geocode q=%r top=%r type=%s score=%.2f", q, (top or {}).get("title"), rtype, score)
    # Precise address/city match wins; otherwise a business/landmark from Discover that matches the words.
    if top and ((score >= 0.9 and rtype in AREA_TYPES) or (score >= MIN_ADDRESS_SCORE and rtype in ADDRESS_TYPES)):
        p = _here_item(top, (lat, lng))
        if p and _near_enough(p, score >= 0.9):
            return [p]
    if disc.status_code == 200:
        for it in disc.json().get("items", []):
            label = f"{it.get('title', '')} {(it.get('address') or {}).get('label', '')}"
            ov = _overlap(q, label)
            p = _here_item(it, (lat, lng)) if ov >= 0.5 else None
            if p and _near_enough(p, ov >= 0.9):
                return [p]
    if top and score >= MIN_HERE_SCORE:
        p = _here_item(top, (lat, lng))
        return [p] if p and _near_enough(p, score >= 0.9) else []
    return []


async def _ors(q: str, lat: float, lng: float) -> list[Place]:
    params = {"api_key": os.environ["ORS_API_KEY"], "text": q, "size": 3, "boundary.country": "US",
              "focus.point.lat": lat, "focus.point.lon": lng}
    r = await client().get("https://api.openrouteservice.org/geocode/search", params=params, timeout=10)
    r.raise_for_status()
    out = []
    for f in r.json().get("features", []):
        props = f.get("properties", {})
        if props.get("confidence", 0) < MIN_ORS_CONFIDENCE and _overlap(q, props.get("label", "")) < 0.5:
            continue
        g_lng, g_lat = f["geometry"]["coordinates"]
        p = _place(f"ors-{props.get('id', props.get('gid', ''))}", props.get("name") or q,
                   g_lat, g_lng, props.get("label"), (lat, lng))
        if _near_enough(p, props.get("confidence", 0) >= 0.9):
            out.append(p)
    return sorted(out, key=lambda p: p.distance_km or 0)


async def _nominatim(q: str, lat: float, lng: float) -> list[Place]:
    d = NOMINATIM_BOX_DEG
    params = {"q": q, "format": "jsonv2", "limit": 5, "countrycodes": "us",
              "viewbox": f"{lng - d},{lat + d},{lng + d},{lat - d}", "bounded": 0}  # prefer around the driver
    headers = {"User-Agent": "TruckBuddy/1.0 (truck copilot demo)"}
    r = await client().get("https://nominatim.openstreetmap.org/search", params=params, headers=headers, timeout=10)
    r.raise_for_status()
    out = [_place(f"osm-{it.get('osm_id')}", it.get("name") or q, float(it["lat"]), float(it["lon"]),
                  it.get("display_name"), (lat, lng))
           for it in r.json() if _overlap(q, it.get("display_name", "")) >= 0.5]
    return sorted((p for p in out if _near_enough(p, False)), key=lambda p: p.distance_km or 0)[:3]


async def geocode(q: str, lat: float, lng: float) -> list[Place]:
    raw, q = q, words_to_digits(q)
    errors = 0
    for key, fn in (("HERE_API_KEY", _here), ("ORS_API_KEY", _ors), (None, _nominatim)):
        if key and not os.environ.get(key):
            continue
        try:
            found = await fn(q, lat, lng)  # a confident "nothing found" is final
            logger.info("geocode raw=%r query=%r via=%s -> %s", raw, q, fn.__name__,
                        f"{found[0].name} | {found[0].address}" if found else "no match")
            return found
        except Exception as e:  # noqa: BLE001 - provider error: try the next geocoder
            logger.warning("%s geocode failed for %r: %s", fn.__name__, q, e)
            errors += 1
    if errors:  # every geocoder that was tried errored: a service problem, not "address not found"
        raise PlacesUnavailable(f"all geocoders failed for {q!r}")
    return []
