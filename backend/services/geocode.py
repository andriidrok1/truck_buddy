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
from services.geo import haversine_km, km_to_mi, without_country
from services.google_places import TRUCK_STOP_NAME, PlacesUnavailable

logger = logging.getLogger("truckbuddy.geocode")

MIN_HERE_SCORE = 0.75
MIN_ADDRESS_SCORE = 0.8  # street / place / postal code / intersection / house number
ADDRESS_TYPES = ("houseNumber", "street", "place", "postalCode", "intersection")
AREA_TYPES = ("locality", "administrativeArea")  # towns need a near-perfect match (wrong-town risk)
MIN_ORS_CONFIDENCE = 0.7
FAR_KM = 500.0  # a weak match this far from the driver is almost always the wrong town
NEAR_KM = 320.0  # ~200 mi: a named place within this beats an equally well named one farther away
AROUND_TIMEOUT_S = 3.0  # the circle search only improves an answer we already have
NOMINATIM_BOX_DEG = 3.0  # viewbox around the driver (preferred, not strict)
STOP = {"the", "of", "to", "and", "in", "at", "on", "st", "street", "ave", "rd", "road", "near", "me", "usa", "us"}
_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado",
    "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
    "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana",
    "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey",
    "nm": "new mexico", "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina", "sd": "south dakota",
    "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}
_STATE_WORDS = set(_STATES) | set(_STATES.values())


def town_query(q: str) -> bool:
    """"Payson, AZ" / "Payson, Arizona": a town named with its state and nothing else. The driver means the town,
    not a street or a business of that name nearer by (HERE ranked "Payson St, Phoenix" and a business in Cave
    Creek over Payson, AZ when asked from Fountain Hills, 2026-11-01). "Pilot, Fernley NV" (a business and its
    town) and chain names are not towns."""
    parts = [p.strip() for p in q.strip().rstrip(".").split(",")]
    if len(parts) != 2 or not parts[0] or re.search(r"\d", q):
        return False
    return parts[1].lower() in _STATE_WORDS and not chain_query(parts[0])


_STREET_WORD = re.compile(r"\b(?:st|street|ave|avenue|rd|road|blvd|boulevard|hwy|highway|dr|drive|way|ln|lane|ct|"
                          r"court|pl|pkwy|parkway|trl|trail|cir|circle|fwy|freeway|interstate|exit)\b", re.I)


def name_query(q: str) -> bool:
    """"Boomtown, NV", "the 49er", "Pilot": a place's name, no number of its own and no street word. HERE's geocoder
    answers such a name with any same-named street in the country; the driver means a place near him."""
    return not re.search(r"\b\d+(?:st|nd|rd|th)?\b", q, re.I) and not _STREET_WORD.search(q)


def chain_query(q: str) -> bool:
    """A bare truck stop chain ("Pilot", "Love's"): never a town of that name (Pilot, NV; Pilot, VA)."""
    return bool(TRUCK_STOP_NAME.fullmatch(q.strip().rstrip(".").lower().removeprefix("the ").strip()))


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


_STATE_CODE = {**{k: k for k in _STATES}, **{v: k for k, v in _STATES.items()}}
# Codes that are also words or common abbreviations ("truck wash in LA", "or", "ok"): a state only after a comma.
_AMBIGUOUS_CODES = {"al", "co", "de", "ga", "hi", "id", "in", "la", "ma", "md", "me", "mo", "ms", "mt", "nd", "ne",
                    "oh", "ok", "or", "pa", "sc", "sd", "va", "wa"}
_TRAILING_STATE = re.compile(r"(?:^|\s)(" + "|".join(sorted(_STATE_CODE, key=len, reverse=True)) + r")\s*$")


def _strip_state(text: str, allow_empty: bool = False) -> tuple[str, Optional[str]]:
    """'boomtown nevada' -> ('boomtown', 'nv'); 'fernley nv' -> ('fernley', 'nv'). A lone state word stays a word
    ('washington') and an ambiguous code ('in la') stays a word, unless after_comma ('Jubitz, OR')."""
    t = text.strip().lower()
    m = _TRAILING_STATE.search(t)
    if not m or (not allow_empty and (not t[:m.start()].strip() or m.group(1) in _AMBIGUOUS_CODES)):
        return t, None
    return t[:m.start()].strip(), _STATE_CODE[m.group(1)]


def _query_words(query: str) -> set[str]:
    """The words a match has to carry: no state named as the place's location, no filler ("Jubitz, OR" is
    {jubitz}: any label with ", OR" in it matched half of it)."""
    head, *tail = query.lower().split(",")
    head = _strip_state(head)[0]
    name = re.sub(r"\s+\b(?:in|near)\s+.+$", "", head)  # "Flying J in Ely": the town is checked by _location
    words = _words(name) or _words(head)
    for part in tail:
        if part.strip() not in ("united states", "usa"):
            words |= _words(_strip_state(part, allow_empty=True)[0])
    return words or _words(query)


def _location(query: str) -> tuple[set[str], Optional[str]]:
    """Town words and state code the driver named: "Flying J in Ely" -> ({ely}, None), "Jubitz, OR" -> ({}, "or"),
    "Boomtown Nevada" -> ({}, "nv"), "Pilot, Fernley NV" -> ({fernley}, "nv")."""
    head, *tail = query.lower().split(",")
    head, state = _strip_state(head)
    town: set[str] = set()
    m = re.search(r"\b(?:in|near)\s+(.+)$", head)
    if m:  # a two-letter "town" ("in LA", "near SF") is an abbreviation no address label spells out
        town |= {w for w in _words(m.group(1)) if len(w) > 2}
    for part in tail:
        if part.strip() in ("united states", "usa"):
            continue
        rest, st = _strip_state(part, allow_empty=True)
        state = st or state
        town |= _words(rest)
    return town, state


def _in_location(label: str, town: set[str], state: Optional[str]) -> bool:
    low = label.lower()
    if town and not town <= _words(low):
        return False
    return not state or state in re.findall(r"[a-z]+", low) or _STATES[state] in low


def _overlap(query: str, text: str) -> float:
    q = _query_words(query)
    return len(q & _words(text)) / len(q) if q else 0.0


def _place(pid: str, name: str, lat: float, lng: float, address: Optional[str], at: tuple[float, float]) -> Place:
    km = haversine_km(at[0], at[1], lat, lng)
    name, address = without_country(name) or name, without_country(address) or None
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
    street = it.get("resultType") == "street"  # a bare street: "49er" became 49ER Trl, Placerville (H9/H23)
    kind = "town-" if town else "street-" if street else ""
    return _place(f"here-{kind}{it.get('id')}", it.get("title") or addr or "Destination",
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


def _town_match(q: str, items: list) -> Optional[dict]:
    """The locality whose name is the town the driver said (the part before the comma), scored near-perfect."""
    want = q.split(",")[0].strip().lower()
    for it in items:
        name = str(it.get("title") or "").split(",")[0].strip().lower()
        if it.get("resultType") in AREA_TYPES and name == want and ((it.get("scoring") or {}).get("queryScore", 0)) >= 0.9:
            return it
    return None


async def _here_once(q: str, lat: float, lng: float) -> list[Place]:
    key = os.environ["HERE_API_KEY"]
    common = {"q": q, "at": f"{lat},{lng}", "limit": 3, "apiKey": key, "in": "countryCode:USA"}
    c = client()
    town = town_query(q)
    calls = [c.get("https://geocode.search.hereapi.com/v1/geocode", params=common, timeout=10),
             c.get("https://discover.search.hereapi.com/v1/discover", params=common, timeout=10)]
    if town:  # the general search ranks a same-named street or business nearer by first; ask for the city itself too
        calls.append(c.get("https://geocode.search.hereapi.com/v1/geocode", params={**common, "types": "city"}, timeout=10))
    geo, disc, *city = await asyncio.gather(*calls, return_exceptions=True)
    if isinstance(geo, BaseException):
        raise geo
    geo.raise_for_status()
    items = geo.json().get("items", [])
    if town:
        candidates = items + (city[0].json().get("items", []) if city and not isinstance(city[0], BaseException) and city[0].status_code == 200 else [])
        hit = _town_match(q, candidates)
        if hit:
            logger.info("here geocode q=%r is a town: %r", q, hit.get("title"))
            p = _here_item(hit, (lat, lng))
            if p:
                return [p]
    if chain_query(q):  # "Pilot" is a truck stop, never the hamlet of Pilot, NV: let Discover's businesses answer
        items = [it for it in items if it.get("resultType") not in AREA_TYPES]
    if isinstance(disc, BaseException):
        disc = None
    top = items[0] if items else None
    score = ((top or {}).get("scoring") or {}).get("queryScore", 0)
    rtype = (top or {}).get("resultType")
    logger.info("here geocode q=%r top=%r type=%s score=%.2f", q, (top or {}).get("title"), rtype, score)
    name = name_query(q)
    # Precise address/city match wins; a bare street only when the driver said a street ("Boomtown" is a casino
    # truck stop at Reno, not Boom Town Dr in Las Vegas, QA 2026-11-05 M-2).
    precise = top and ((score >= 0.9 and rtype in AREA_TYPES) or (score >= MIN_ADDRESS_SCORE and rtype in ADDRESS_TYPES))
    top_place = _here_item(top, (lat, lng)) if top and (precise or score >= MIN_HERE_SCORE) else None
    if top_place and not _near_enough(top_place, score >= 0.9):
        top_place = None
    if precise and top_place and not (name and rtype not in AREA_TYPES and
                                      (rtype == "street" or (top_place.distance_km or 0) > NEAR_KM)):
        return [top_place]
    disc_items = disc.json().get("items", []) if disc is not None and disc.status_code == 200 else []
    if not name:  # an address: a business from Discover that matches the words, then the geocoder's own answer
        pois = [p for _, p in _discover_hits(q, disc_items, (lat, lng))]
        return (pois + ([top_place] if top_place else []))[:1]
    return await _pick_named(q, lat, lng, disc_items, top, top_place, demoted=bool(precise and top_place))


async def _pick_named(q: str, lat: float, lng: float, disc_items: list, top: Optional[dict],
                      top_place: Optional[Place], demoted: bool) -> list[Place]:
    """A place's name: the best name match wins, the town or state the driver named first ("Flying J in Ely" is
    the one in Ely, not the nearer one in Sparks); distance only orders equally good matches (a place over a
    street among those). When the winner is far and the driver named no place it sits in, Discover is asked once
    around the driver for an equally good match. A pick that is not the geocoder's own precise answer is marked
    (picked_nearby) so the agent reads it back."""
    town, state = _location(q)
    cands: list[_Cand] = []
    for ov, p in _discover_hits(q, disc_items, (lat, lng)):
        cands.append((ov, _in_location(f"{p.name} {p.address or ''}", town, state), is_street(p), False, p))
    if top_place:
        label = f"{(top or {}).get('title', '')} {((top or {}).get('address') or {}).get('label', '')}"
        cands.append((_overlap(q, label), _in_location(label, town, state), is_street(top_place), True, top_place))
    if not cands:
        return []
    best = _best(cands)
    best_ov, best_loc = next((c[0], c[1]) for c in cands if c[4] is best)
    located = bool(town or state) and best_loc and best_ov >= 0.9
    if (best.distance_km or 0) > NEAR_KM and not (located and town):
        closer = await _discover_around(q, lat, lng, max(0.9, 0.9 * best_ov), town, state)
        if closer:
            logger.info("here discover around q=%r -> %r (%.0f km)", q, closer.name, closer.distance_km or 0)
            return [_marked(closer)]
    return [_marked(best) if demoted and best is not top_place else best]


_Cand = tuple[float, bool, bool, bool, Place]  # (overlap, in the named town/state, a street, the geocoder's own, place)


def _best(cands: list[_Cand]) -> Place:
    """In the named town/state first, then the best overlap; among overlaps within 90% of it a place before a
    street, the geocoder's own answer before the same thing from Discover, then the nearest."""
    cands = sorted(cands, key=lambda c: (not c[1], -c[0]))
    lead_ov, lead_loc = cands[0][0], cands[0][1]
    group = [c for c in cands if c[1] == lead_loc and c[0] >= 0.9 * lead_ov]
    return min(group, key=lambda c: (c[2], not c[3], c[4].distance_km or 0))[4]


class NearbyPick(Place):
    """A Place picked instead of the geocoder's own answer. Same fields and id (is_street / is_town still read the
    id prefix); only the type says "read it back"."""


def _marked(p: Place) -> Place:
    return NearbyPick(**p.model_dump())


def picked_nearby(place) -> bool:
    """A name the geocoder answered with something else (a street, a far match) that was swapped for a place
    near the driver: read back before routing ("Elm" -> Elm Street Diner instead of Elm St)."""
    return isinstance(place, NearbyPick)


def _discover_hits(q: str, items: list, at: tuple[float, float]) -> list[tuple[float, Place]]:
    """(overlap, place) for the Discover items whose title/address carries the words of the query, HERE's order."""
    hits: list[tuple[float, Place]] = []
    for it in items:
        label = f"{it.get('title', '')} {(it.get('address') or {}).get('label', '')}"
        ov = _overlap(q, label)
        p = _here_item(it, at) if ov >= 0.5 else None
        if p and _near_enough(p, ov >= 0.9):
            hits.append((ov, p))
    return hits


async def _discover_around(q: str, lat: float, lng: float, min_ov: float, town: set[str],
                           state: Optional[str]) -> Optional[Place]:
    """Discover bounded to NEAR_KM around the driver: the nearest place there that matches the name at least
    min_ov and sits in the town/state the driver named. Whatever is closest in a circle often shares only the
    state with the query, hence the bar. Gives up after AROUND_TIMEOUT_S: the far answer stands."""
    params = {"q": q, "in": f"circle:{lat},{lng};r={int(NEAR_KM * 1000)}", "limit": 5,
              "apiKey": os.environ["HERE_API_KEY"]}
    try:
        r = await asyncio.wait_for(client().get("https://discover.search.hereapi.com/v1/discover", params=params,
                                                timeout=AROUND_TIMEOUT_S), AROUND_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - incl. the timeout: the far answer still stands
        logger.info("here discover around failed for %r: %s", q, type(e).__name__)
        return None
    if r.status_code != 200:
        return None
    hits = [p for ov, p in _discover_hits(q, r.json().get("items", []), (lat, lng))
            if ov >= min_ov and (p.distance_km or 0) <= NEAR_KM
            and _in_location(f"{p.name} {p.address or ''}", town, state)]
    return min(hits, key=lambda p: p.distance_km or 0) if hits else None


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


REVGEOCODE_URL = "https://revgeocode.search.hereapi.com/v1/revgeocode"


async def where(lat: float, lng: float) -> Optional[dict]:
    """The road and town at a point, from HERE reverse geocoding: {"street", "city", "state"} (any may be None),
    or None without a HERE key or on any failure."""
    key = os.environ.get("HERE_API_KEY")
    if not key:
        return None
    try:
        r = await client().get(REVGEOCODE_URL, params={"at": f"{lat},{lng}", "limit": 1, "lang": "en-US", "apiKey": key},
                               timeout=6)
        r.raise_for_status()
        items = r.json().get("items") or []
    except Exception as e:  # noqa: BLE001 - "where am I" says it can't tell
        logger.info("reverse geocode failed: %s", type(e).__name__)
        return None
    if not items:
        return None
    a = items[0].get("address") or {}
    return {"street": a.get("street"), "city": a.get("city") or a.get("county"), "state": a.get("state")}


def is_street(place) -> bool:
    """A bare street from HERE (no house number): what a short business name geocodes to when nothing better matches."""
    return str(getattr(place, "id", "")).startswith("here-street-")
