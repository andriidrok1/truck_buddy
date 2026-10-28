"""Google Places (New) Text Search provider: real places with rating and description.

Used when GOOGLE_MAPS_API_KEY is set (see services/places.py).
"""
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

from services.http import client

from models import Category, Place, PlacesResponse, Review
from services.flags import on
from services.geo import haversine_km, km_to_mi
from services import tpims
from services.places import DATA_ONLY, search_data

logger = logging.getLogger("truckbuddy.google")

URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.rating,places.userRatingCount,"
    "places.editorialSummary,places.formattedAddress,places.location,places.types,places.primaryType,"
    "places.fuelOptions"  # same Enterprise + Atmosphere SKU as editorialSummary: no extra cost
)
REVIEW_TEXT_MAX = 300
MAX_REVIEWS = 5


def field_mask() -> str:
    """Fields asked of Google; reviews are in the same SKU as editorialSummary (PLACE_REVIEWS flag)."""
    return FIELD_MASK + (",places.reviews" if on("PLACE_REVIEWS") else "")


def reviews_from(raw) -> list[Review]:
    """Google `reviews[]` -> Review list (max 5), skipping anything without text."""
    out: list[Review] = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        text = ((r.get("text") or {}).get("text") if isinstance(r.get("text"), dict) else r.get("text")) or ""
        text = re.sub(r"\s+", " ", str(text)).strip()
        if not text:
            continue
        if len(text) > REVIEW_TEXT_MAX:
            text = text[:REVIEW_TEXT_MAX].rsplit(" ", 1)[0] + "…"
        author = r.get("authorAttribution") or {}
        rating = r.get("rating")
        out.append(Review(author=str(author.get("displayName") or "A Google user"),
                          rating=int(rating) if isinstance(rating, (int, float)) else None,
                          text=text, uri=r.get("googleMapsUri") or author.get("uri"), time=r.get("publishTime")))
        if len(out) >= MAX_REVIEWS:
            break
    return out


DIESEL_MAX_AGE_S = 7 * 86400  # a price older than this is not worth saying out loud
# Google fuel types that mean diesel, best first. TRUCK_DIESEL = the truck lanes; the rest = car pumps.
DIESEL_TYPES = (("TRUCK_DIESEL", "truck"), ("DIESEL", "auto"), ("DIESEL_PLUS", "auto"), ("BIO_DIESEL", "auto"))
# Names that mark a real truck stop (vs. an ordinary gas station Google tags as truck_stop).
TRUCK_STOP_NAME = re.compile(
    r"\b(?:love'?s|pilot|flying j|ta|petro|sapp bros|roady'?s|truck ?stop|travel (?:center|centre|plaza))\b", re.I)
# truck_stop results that are something else: washes, scales, diners, offices.
NOT_TRUCK_STOP_NAME = re.compile(r"truck ?wash|\bscales?\b|restaurant|taqueria|\btacos?\b|\btech\b|office", re.I)
NOT_TRUCK_STOP_TYPES = re.compile(r"restaurant|^cafe$|^car_wash$|office|^meal_", re.I)  # checked on primaryType only
FUEL_TYPES = {"gas_station", "fuel_station", "truck_stop_fuel"}
SAME_PLACE_M = 60  # results closer than this are one place
SAME_NAME_M = 500  # same name this close: one place (Google pin vs curated entrance)
TEXT_QUERY = {
    "hotel": "hotel motel with truck parking",
    "truck_stop": "truck stop",
    "rest_area": "rest area",
    "walmart": "Walmart Supercenter",
    "truck_wash": "truck wash",
}
# Strict Google place types so "truck stop" doesn't return diners or city gas stations.
# Categories without a type are text-only searches filtered by name (KEEP_NAME).
INCLUDED_TYPE = {"hotel": "lodging", "truck_stop": "truck_stop", "rest_area": "rest_stop", "truck_wash": "car_wash"}
# A result of these categories is kept only when its name says what it is.
KEEP_NAME = {
    "walmart": re.compile(r"\bwalmart\s+supercenter\b", re.I),  # Neighborhood Markets have no lot to speak of
    "truck_wash": re.compile(r"\btruck|\btrailer|blue beacon|\bsemi\b|\brig\b", re.I),
}
DEFAULT_RADIUS_KM = 25.0
WIDE_RADIUS_KM = 50.0  # Google max bias radius is 50 km
MAX_RESULTS = 10
SEARCH_TTL_S = 600
CACHE_MAX = 200


class PlacesUnavailable(Exception):
    pass


def diesel_from_fuel_options(fuel: Optional[dict], now: Optional[float] = None) -> Optional[tuple[float, str, str]]:
    """(price_usd_per_gal, kind, update_time) from a Google `fuelOptions` object, or None.
    Prefers the truck-lane price; skips prices older than DIESEL_MAX_AGE_S or not in USD."""
    prices = (fuel or {}).get("fuelPrices") or []
    now = time.time() if now is None else now
    for want, kind in DIESEL_TYPES:
        for fp in prices:
            if fp.get("type") != want:
                continue
            money = fp.get("price") or {}
            if money.get("currencyCode", "USD") != "USD":
                continue
            try:
                price = int(money.get("units", 0)) + int(money.get("nanos", 0)) / 1e9
                updated = fp.get("updateTime") or ""
                age = now - datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp() if updated else 0.0
            except (TypeError, ValueError):
                continue
            if price <= 0 or age > DIESEL_MAX_AGE_S:
                continue
            return round(price, 3), kind, updated
    return None


def truck_stop_reject(name: str, types: list[str], primary: str = "") -> Optional[str]:
    """Why a Google truck_stop result isn't a real truck stop, or None to keep it."""
    chain = bool(TRUCK_STOP_NAME.search(name))
    if not chain and NOT_TRUCK_STOP_NAME.search(name):
        return "name"
    if not chain and primary and NOT_TRUCK_STOP_TYPES.search(primary):
        return "type"
    if not chain and not ("truck_stop" in types and FUEL_TYPES & set(types)):
        return "no fuel/chain"
    return None


def _same(p: Place, q: Place) -> bool:
    m = haversine_km(p.lat, p.lng, q.lat, q.lng) * 1000
    return m <= SAME_PLACE_M or (m <= SAME_NAME_M and p.name.strip().lower() == q.name.strip().lower())


def dedupe_nearby(places: list[Place]) -> list[Place]:
    """One place per location (60 m, or 500 m with the same name): keep a curated one, then a truck-stop name."""
    rank = lambda p: (not p.id.startswith(("ts-", "ht-", "ra-")), not TRUCK_STOP_NAME.search(p.name))  # noqa: E731
    out: list[Place] = []
    for p in sorted(places, key=rank):
        if not any(_same(p, q) for q in out):
            out.append(p)
    return sorted(out, key=lambda x: x.distance_km or 0)


class GooglePlacesProvider:
    source = "google"

    def __init__(self) -> None:
        self._seen: dict[str, Place] = {}
        self._searches: dict[tuple, tuple[float, PlacesResponse]] = {}

    def _remember(self, place: Place) -> None:
        if len(self._seen) >= CACHE_MAX * 10:
            self._seen.pop(next(iter(self._seen)))
        self._seen[place.id] = place

    async def _query(self, category: Category, lat: float, lng: float, radius_km: float) -> list[Place]:
        body = {
            "textQuery": TEXT_QUERY[category],
            "pageSize": MAX_RESULTS,
            "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_km * 1000}},
        }
        if category in INCLUDED_TYPE:
            body["includedType"] = INCLUDED_TYPE[category]
            body["strictTypeFiltering"] = True
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": os.environ["GOOGLE_MAPS_API_KEY"],
            "X-Goog-FieldMask": field_mask(),
        }
        r = await client().post(URL, headers=headers, json=body, timeout=15)
        if r.is_error:
            logger.error("Google Places error %s: %s", r.status_code, r.text[:300])
            raise PlacesUnavailable("Places provider request failed")

        out = []
        dedupe = set()
        raw = []
        for p in r.json().get("places", []):
            loc = p.get("location") or {}
            if "latitude" not in loc:
                continue
            km = haversine_km(lat, lng, loc["latitude"], loc["longitude"])
            name = (p.get("displayName") or {}).get("text") or "Unnamed place"
            types = p.get("types") or []
            primary = p.get("primaryType") or ""
            keep = KEEP_NAME.get(category)
            drop = "far" if km > radius_km else (
                truck_stop_reject(name, types, primary) if category == "truck_stop" else
                "name" if keep and not keep.search(name) else None)
            raw.append(f"{name} [{primary}: {','.join(types[:5])}] {km:.1f}km{' DROP ' + drop if drop else ''}")
            if drop:  # bias can return far-away results
                continue
            key = (name.lower(), (p.get("formattedAddress") or "").lower())
            if key in dedupe:
                continue
            dedupe.add(key)
            diesel = diesel_from_fuel_options(p.get("fuelOptions"))
            place = Place(
                id=p["id"],
                name=name,
                category=category,
                lat=loc["latitude"],
                lng=loc["longitude"],
                address=p.get("formattedAddress"),
                rating=p.get("rating"),
                rating_count=p.get("userRatingCount"),
                summary=(p.get("editorialSummary") or {}).get("text"),
                diesel_price=diesel[0] if diesel else None,
                diesel_kind=diesel[1] if diesel else None,
                diesel_updated=diesel[2] if diesel else None,
                distance_km=round(km, 2),
                distance_mi=round(km_to_mi(km), 1),
                reviews=reviews_from(p.get("reviews")),
            )
            self._remember(place)
            out.append(place)
        logger.info("google %s @%.4f,%.4f r=%skm raw: %s", category, lat, lng, radius_km, " | ".join(raw) or "none")
        return self._merge_curated(category, lat, lng, radius_km, out)

    def _merge_curated(self, category: Category, lat: float, lng: float, radius_km: float,
                       found: list[Place]) -> list[Place]:
        """Curated places (checked verdict + entrance) join the results and win within 60 m."""
        from services.places import curated
        mine = [p for p in curated(category, lat, lng) if (p.distance_km or 0) <= radius_km]
        for p in mine:
            self._remember(p)
        return dedupe_nearby(found + mine)

    async def search(self, category: Category, lat: float, lng: float) -> PlacesResponse:
        if category in DATA_ONLY:  # CAT scales, weigh stations: our data file, no billable search
            res = search_data(category, lat, lng, "data")
            for p in res.places:
                self._remember(p)
            return res
        # ~1 km grid cache so repeated taps/turns don't trigger new billable searches.
        ck = (category, round(lat, 2), round(lng, 2))
        hit = self._searches.get(ck)
        if hit and time.monotonic() - hit[0] < SEARCH_TTL_S:
            return hit[1]
        radius, widened = DEFAULT_RADIUS_KM, False
        found = await self._query(category, lat, lng, radius)
        if not found:
            radius, widened = WIDE_RADIUS_KM, True
            found = await self._query(category, lat, lng, radius)
        tpims.attach(found)  # live counts / capacity for truck stops and rest areas, when a sensor site is near
        res = PlacesResponse(category=category, radius_km=radius, widened=widened, places=found, source=self.source)
        if len(self._searches) >= CACHE_MAX:
            self._searches.pop(next(iter(self._searches)))
        self._searches[ck] = (time.monotonic(), res)
        return res

    async def get(self, place_id: str, lat: float, lng: float) -> Optional[Place]:
        return self._seen.get(place_id)
