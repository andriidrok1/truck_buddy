"""Places provider. MOCK implementation: real places in the Bay Area, along I-80 and around Sacramento, CA.

Real implementation to plug in later: OpenStreetMap Overpass (same interface).
"""
import os
from typing import List, Optional, Protocol

from models import Category, Place, PlacesResponse
from services.geo import haversine_km, km_to_mi

SACRAMENTO = (38.5816, -121.4944)
SAN_FRANCISCO = (37.7749, -122.4194)
ANCHORS = (SAN_FRANCISCO, SACRAMENTO)
DEFAULT_RADIUS_KM = 25.0
WIDE_RADIUS_KM = 60.0
MIN_RESULTS = 3
# Demo data covers the Bay Area and I-80 to Sacramento. Farther than this from both anchors
# there's nothing real to show: return no places (never shifted/fake pins).
COVERAGE_KM = 150.0
NO_PLACES = "No demo places here. Demo data covers the Bay Area and Sacramento."

# Real places (HERE Discover, addresses geocode in HERE). Coordinates are never moved:
# the satellite parking check looks exactly here.
MOCK_PLACES = [
    # Truck stops (blue). Sacramento / I-80 / I-5 / CA-99
    {"id": "ts-49er", "name": "Sacramento 49er Travel Plaza", "category": "truck_stop", "lat": 38.61736, "lng": -121.53861,
     "tags": ["53 ft parking", "Truck access", "Diesel", "Showers"], "address": "2828 El Centro Rd, Sacramento (I-80 at W El Camino Ave)"},
    {"id": "ts-westsac", "name": "West Sac Truck Stop", "category": "truck_stop", "lat": 38.57602, "lng": -121.57636,
     "tags": ["Truck access", "Diesel"], "address": "4800 W Capitol Ave, West Sacramento"},
    {"id": "ts-roadys-westsac", "name": "Roady's Truck Stop", "category": "truck_stop", "lat": 38.57955, "lng": -121.54827,
     "tags": ["Truck access", "Diesel"], "address": "2816 W Capitol Ave, West Sacramento"},
    {"id": "ts-sacvalley", "name": "Sac Valley Truck Stop", "category": "truck_stop", "lat": 38.4713, "lng": -121.41726,
     "tags": ["Truck access", "Diesel"], "address": "7891 Stockton Blvd, Sacramento"},
    {"id": "ts-flyingj-lodi", "name": "Flying J Travel Center", "category": "truck_stop", "lat": 38.11716, "lng": -121.39385,
     "tags": ["53 ft parking", "Diesel", "24/7"], "address": "15100 N Thornton Rd, Lodi"},
    {"id": "ts-petro-corning", "name": "Petro Stopping Center", "category": "truck_stop", "lat": 39.90611, "lng": -122.19623,
     "tags": ["53 ft parking", "Truck access", "Diesel", "Showers"], "address": "2151 South Ave, Corning"},
    {"id": "ts-loves-williams", "name": "Love's Travel Stop", "category": "truck_stop", "lat": 39.16765, "lng": -122.14382,
     "tags": ["Truck access", "Diesel"], "address": "100 Marguerite St, Williams"},
    # Truck stops: Bay Area (Port of Oakland / I-880) and I-80 Fairfield
    {"id": "ts-sfoakland", "name": "SF Oakland Truck Stop", "category": "truck_stop", "lat": 37.74891, "lng": -122.19173,
     "tags": ["Truck access", "Diesel"], "address": "8255 San Leandro St, Oakland"},
    {"id": "ts-valleyoil-mv", "name": "Valley Oil", "category": "truck_stop", "lat": 37.3815, "lng": -122.06982,
     "tags": ["Diesel", "Truck access"], "address": "785 Yuba Dr, Mountain View"},
    {"id": "ts-western-sanleandro", "name": "Western States Oil", "category": "truck_stop", "lat": 37.70687, "lng": -122.1594,
     "tags": ["Diesel", "Truck access"], "address": "2709 Teagarden St, San Leandro"},
    {"id": "ts-fairfield", "name": "Fairfield Travel Center", "category": "truck_stop", "lat": 38.25706, "lng": -122.06737,
     "tags": ["Truck access", "Diesel"], "address": "1351 Oliver Rd, Fairfield (I-80)"},
    # Hotels (purple): motels at freeway exits
    {"id": "ht-baybreeze", "name": "Bay Breeze Inn", "category": "hotel", "lat": 37.76387, "lng": -122.21577,
     "tags": ["Truck parking nearby"], "address": "4919 Coliseum Way, Oakland"},
    {"id": "ht-coliseum", "name": "Coliseum Motel", "category": "hotel", "lat": 37.7658, "lng": -122.2171,
     "tags": ["Truck parking nearby"], "address": "4801 Coliseum Way, Oakland"},
    {"id": "ht-motel6-fairfield", "name": "Motel 6 Fairfield", "category": "hotel", "lat": 38.25943, "lng": -122.06578,
     "tags": ["Truck parking nearby"], "address": "1473 Holiday Ln, Fairfield (I-80)"},
    {"id": "ht-motel6-vacaville", "name": "Motel 6 Vacaville", "category": "hotel", "lat": 38.3721, "lng": -121.95126,
     "tags": ["Parking"], "address": "107 Lawrence Dr, Vacaville (I-80)"},
    {"id": "ht-crest-westsac", "name": "Crest Motel", "category": "hotel", "lat": 38.5827, "lng": -121.51999,
     "tags": ["Parking"], "address": "958 W Capitol Ave, West Sacramento"},
    {"id": "ht-crossroads", "name": "Crossroads Inn", "category": "hotel", "lat": 38.59582, "lng": -121.50509,
     "tags": ["Parking"], "address": "221 Jibboom St, Sacramento"},
    # Rest areas (green)
    {"id": "ra-hunterhill", "name": "Hunter Hill Rest Area", "category": "rest_area", "lat": 38.15428, "lng": -122.21707,
     "tags": ["Truck parking", "Restrooms"], "address": "I-80 Westbound, Vallejo"},
    {"id": "ra-crystalsprings", "name": "Crystal Springs Rest Area", "category": "rest_area", "lat": 37.53937, "lng": -122.36464,
     "tags": ["Restrooms"], "address": "I-280, Burlingame"},
    {"id": "ra-elkhorn-s", "name": "Elkhorn Rest Area (southbound)", "category": "rest_area", "lat": 38.67183, "lng": -121.61215,
     "tags": ["Truck parking", "Restrooms"], "address": "I-5 Southbound, Sacramento"},
    {"id": "ra-elkhorn-n", "name": "Elkhorn Rest Area (northbound)", "category": "rest_area", "lat": 38.67148, "lng": -121.61224,
     "tags": ["Truck parking", "Restrooms"], "address": "I-5 Northbound, Sacramento"},
    {"id": "ra-dunnigan", "name": "Dunnigan Rest Area", "category": "rest_area", "lat": 38.89275, "lng": -121.97813,
     "tags": ["Truck parking", "Restrooms"], "address": "I-5 Northbound, Dunnigan"},
]


def _all(lat: float, lng: float) -> List[Place]:
    out = []
    for raw in MOCK_PLACES:
        p = Place(**raw)  # never moved
        km = haversine_km(lat, lng, p.lat, p.lng)
        p.distance_km = round(km, 2)
        p.distance_mi = round(km_to_mi(km), 1)
        out.append(p)
    return out


def curated(category: Category, lat: float, lng: float) -> List[Place]:
    """Curated places of a category with distances (merged into the Google results)."""
    return [p for p in _all(lat, lng) if p.category == category]


class PlacesProvider(Protocol):
    source: str

    async def search(self, category: Category, lat: float, lng: float) -> PlacesResponse: ...

    async def get(self, place_id: str, lat: float, lng: float) -> Optional[Place]: ...


class MockPlacesProvider:
    source = "mock"

    def _all(self, lat: float, lng: float) -> List[Place]:
        return _all(lat, lng)

    async def search(self, category: Category, lat: float, lng: float) -> PlacesResponse:
        if min(haversine_km(lat, lng, *a) for a in ANCHORS) > COVERAGE_KM:
            return PlacesResponse(category=category, radius_km=0, widened=False, places=[], source=self.source,
                                  message=NO_PLACES)
        candidates = [p for p in self._all(lat, lng) if p.category == category]
        radius, widened = DEFAULT_RADIUS_KM, False
        found = [p for p in candidates if p.distance_km <= radius]
        if len(found) < MIN_RESULTS:  # widen until there's a real choice
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
