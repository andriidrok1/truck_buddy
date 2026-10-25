"""Places provider. MOCK implementation: hardcoded places around Sacramento, CA.

Real implementation to plug in later: OpenStreetMap Overpass (same interface).
"""
import os
from typing import List, Optional, Protocol

from models import Category, Place, PlacesResponse
from services.geo import haversine_km, km_to_mi

SACRAMENTO = (38.5816, -121.4944)
DEFAULT_RADIUS_KM = 25.0
WIDE_RADIUS_KM = 60.0
# If the driver is farther than this from Sacramento, the mock pins are shifted
# around the driver so the demo works anywhere.
MOCK_ANCHOR_KM = 80.0

MOCK_PLACES = [
    # Truck stops (blue)
    {"id": "ts-capitol", "name": "Capitol Truck Plaza", "category": "truck_stop", "lat": 38.6105, "lng": -121.5390,
     "tags": ["53 ft parking", "Truck access", "Diesel"], "address": "I-80 Exit 85, West Sacramento"},
    {"id": "ts-sierra", "name": "Sierra Fuel & Truck Stop", "category": "truck_stop", "lat": 38.5480, "lng": -121.4120,
     "tags": ["Truck access", "Diesel", "Showers"], "address": "US-50 & Howe Ave"},
    {"id": "ts-delta", "name": "Delta Junction Truck Stop", "category": "truck_stop", "lat": 38.4990, "lng": -121.5100,
     "tags": ["53 ft parking", "Diesel", "24/7"], "address": "I-5 Exit 512"},
    {"id": "ts-northgate", "name": "Northgate Travel Center", "category": "truck_stop", "lat": 38.6490, "lng": -121.4590,
     "tags": ["Truck access", "Diesel"], "address": "Northgate Blvd & I-80"},
    # Hotels (purple)
    {"id": "ht-oakgrove", "name": "Oak Grove Inn", "category": "hotel", "lat": 38.6230, "lng": -121.4480,
     "tags": ["53 ft parking", "Truck access"], "address": "Arden Way, Sacramento"},
    {"id": "ht-riverside", "name": "Riverside Motor Lodge", "category": "hotel", "lat": 38.5700, "lng": -121.5250,
     "tags": ["Truck access"], "address": "Jefferson Blvd, West Sacramento"},
    {"id": "ht-capitolhwy", "name": "Capitol Hwy Motel", "category": "hotel", "lat": 38.5420, "lng": -121.4580,
     "tags": ["Truck parking"], "address": "Stockton Blvd, Sacramento"},
    # Rest areas (green)
    {"id": "ra-elkgrove", "name": "Elk Grove Rest Area", "category": "rest_area", "lat": 38.4300, "lng": -121.4300,
     "tags": ["Truck parking", "Restrooms"], "address": "SR-99 Northbound"},
    {"id": "ra-yolo", "name": "Yolo Causeway Rest Stop", "category": "rest_area", "lat": 38.5650, "lng": -121.6100,
     "tags": ["53 ft parking", "Restrooms"], "address": "I-80 Westbound"},
    {"id": "ra-american", "name": "American River Rest Area", "category": "rest_area", "lat": 38.6000, "lng": -121.3500,
     "tags": ["Truck parking"], "address": "US-50 Eastbound"},
]


class PlacesProvider(Protocol):
    source: str

    async def search(self, category: Category, lat: float, lng: float) -> PlacesResponse: ...

    async def get(self, place_id: str, lat: float, lng: float) -> Optional[Place]: ...


class MockPlacesProvider:
    source = "mock"

    def _shift(self, lat: float, lng: float):
        if haversine_km(lat, lng, *SACRAMENTO) > MOCK_ANCHOR_KM:
            return lat - SACRAMENTO[0], lng - SACRAMENTO[1]
        return 0.0, 0.0

    def _all(self, lat: float, lng: float) -> List[Place]:
        dlat, dlng = self._shift(lat, lng)
        out = []
        for raw in MOCK_PLACES:
            p = Place(**{**raw, "lat": raw["lat"] + dlat, "lng": raw["lng"] + dlng})
            km = haversine_km(lat, lng, p.lat, p.lng)
            p.distance_km = round(km, 2)
            p.distance_mi = round(km_to_mi(km), 1)
            out.append(p)
        return out

    async def search(self, category: Category, lat: float, lng: float) -> PlacesResponse:
        candidates = [p for p in self._all(lat, lng) if p.category == category]
        radius, widened = DEFAULT_RADIUS_KM, False
        found = [p for p in candidates if p.distance_km <= radius]
        if not found:
            radius, widened = WIDE_RADIUS_KM, True
            found = [p for p in candidates if p.distance_km <= radius]
        found.sort(key=lambda p: p.distance_km)
        return PlacesResponse(category=category, radius_km=radius, widened=widened, places=found, source=self.source)

    async def get(self, place_id: str, lat: float, lng: float) -> Optional[Place]:
        return next((p for p in self._all(lat, lng) if p.id == place_id), None)


_mock: PlacesProvider = MockPlacesProvider()
_google: Optional[PlacesProvider] = None


def get_places_provider() -> PlacesProvider:
    """Real Google Places when GOOGLE_MAPS_API_KEY is set, otherwise demo places."""
    global _google
    if not os.environ.get("GOOGLE_MAPS_API_KEY"):
        return _mock
    if _google is None:
        from services.google_places import GooglePlacesProvider
        _google = GooglePlacesProvider()
    return _google
