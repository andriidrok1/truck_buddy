"""Google Places (New) Text Search provider: real places with rating and description.

Used when GOOGLE_MAPS_API_KEY is set (see services/places.py).
"""
import logging
import os
import time
from typing import Optional

from services.http import client

from models import Category, Place, PlacesResponse
from services.geo import haversine_km, km_to_mi

logger = logging.getLogger("truckbuddy.google")

URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.rating,places.userRatingCount,"
    "places.editorialSummary,places.formattedAddress,places.location"
)
TEXT_QUERY = {
    "hotel": "hotel motel with truck parking",
    "truck_stop": "truck stop",
    "rest_area": "rest area",
}
# Strict Google place types so "truck stop" doesn't return diners or city gas stations.
INCLUDED_TYPE = {"hotel": "lodging", "truck_stop": "truck_stop", "rest_area": "rest_stop"}
DEFAULT_RADIUS_KM = 25.0
WIDE_RADIUS_KM = 50.0  # Google max bias radius is 50 km
MAX_RESULTS = 10
SEARCH_TTL_S = 600
CACHE_MAX = 200


class PlacesUnavailable(Exception):
    pass


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
            "includedType": INCLUDED_TYPE[category],
            "strictTypeFiltering": True,
            "pageSize": MAX_RESULTS,
            "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_km * 1000}},
        }
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": os.environ["GOOGLE_MAPS_API_KEY"],
            "X-Goog-FieldMask": FIELD_MASK,
        }
        r = await client().post(URL, headers=headers, json=body, timeout=15)
        if r.is_error:
            logger.error("Google Places error %s: %s", r.status_code, r.text[:300])
            raise PlacesUnavailable("Places provider request failed")

        out = []
        dedupe = set()
        for p in r.json().get("places", []):
            loc = p.get("location") or {}
            if "latitude" not in loc:
                continue
            km = haversine_km(lat, lng, loc["latitude"], loc["longitude"])
            if km > radius_km:  # bias can return far-away results
                continue
            name = (p.get("displayName") or {}).get("text") or "Unnamed place"
            key = (name.lower(), (p.get("formattedAddress") or "").lower())
            if key in dedupe:
                continue
            dedupe.add(key)
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
                distance_km=round(km, 2),
                distance_mi=round(km_to_mi(km), 1),
            )
            self._remember(place)
            out.append(place)
        out.sort(key=lambda x: x.distance_km)
        return out

    async def search(self, category: Category, lat: float, lng: float) -> PlacesResponse:
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
        res = PlacesResponse(category=category, radius_km=radius, widened=widened, places=found, source=self.source)
        if len(self._searches) >= CACHE_MAX:
            self._searches.pop(next(iter(self._searches)))
        self._searches[ck] = (time.monotonic(), res)
        return res

    async def get(self, place_id: str, lat: float, lng: float) -> Optional[Place]:
        return self._seen.get(place_id)
