"""Places providers.

- OwnPlacesProvider (PLACES_PROVIDER=own): our own US data in backend/data/places/<category>.json, built monthly
  by scripts/build_places.py from open sources we may store, speak and show on any map (AllThePlaces chain
  locations, FHWA NTAD, Overture). No per-search cost.
- GooglePlacesProvider (services/google_places.py): Google Places text search; its terms forbid speaking, storing
  and showing results on a non-Google map, which the copilot does (2026-11-03), so it is being replaced.
- MockPlacesProvider: real places in the Bay Area, along I-80 and around Sacramento, for keyless dev boxes.

All of them serve CAT scales and weigh stations from backend/data/<category>.json (scripts/import_*.py).
"""
import json
import logging
import math
import os
from pathlib import Path
from typing import Callable, List, Optional, Protocol

from models import Category, Place, PlacesResponse
from services.geo import haversine_km, km_to_mi

logger = logging.getLogger("truckbuddy.places")

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
NO_DATA = "I don't have {what} data loaded yet."

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Categories served from a data file only (no Google text search: it returns junk for these).
DATA_ONLY: tuple[Category, ...] = ("cat_scale", "weigh_station")
DATA_RADII_KM = (50.0, 150.0, 400.0)  # these are sparse: widen until there's something to say
PLACES_DIR = DATA_DIR / "places"
OWN_RADII_KM = (25.0, 60.0, 150.0)  # truck stops, rest areas, Walmart, motels: near first, then wider
OWN_MAX = 10
NAMED_MAX_KM = 150.0  # "find a Love's": the chain's nearest within this, past the radius a plain search stops at
_data_cache: dict[str, Optional[list[dict]]] = {}


def data_rows(category: str) -> list[dict]:
    """Rows of backend/data/<category>.json (cached); [] when the file is missing or broken."""
    if category not in _data_cache:
        path = DATA_DIR / f"{category}.json"
        rows: Optional[list[dict]] = None
        if path.exists():
            try:
                loaded = json.loads(path.read_text())
                rows = [r for r in loaded if isinstance(r, dict)] if isinstance(loaded, list) else None
            except (OSError, ValueError) as e:
                logger.warning("places data %s unreadable: %s", path.name, e)
        _data_cache[category] = rows
    return _data_cache.get(category) or []


def has_data(category: str) -> bool:
    return bool(data_rows(category))


def _with_distance(raw: dict, lat: float, lng: float) -> Place:
    p = Place(**raw)  # never moved
    km = haversine_km(lat, lng, p.lat, p.lng)
    p.distance_km = round(km, 2)
    p.distance_mi = round(km_to_mi(km), 1)
    return p


def search_data(category: Category, lat: float, lng: float, source: str) -> PlacesResponse:
    """Nearest rows of a data-only category, widening the radius until a few are found."""
    rows = data_rows(category)
    if not rows:
        return PlacesResponse(category=category, radius_km=0, widened=False, places=[], source=source,
                              message=NO_DATA.format(what=category.replace("_", " ")))
    places = sorted((_with_distance(r, lat, lng) for r in rows), key=lambda p: p.distance_km or 0)
    radius, widened = DATA_RADII_KM[0], False
    for radius in DATA_RADII_KM:
        found = [p for p in places if (p.distance_km or 0) <= radius]
        if len(found) >= MIN_RESULTS:
            break
        widened = True
    return PlacesResponse(category=category, radius_km=radius, widened=widened, places=found[:10], source=source)


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
    # Truck stops: Bay Area (Port of Oakland / I-880)
    {"id": "ts-sfoakland", "name": "SF Oakland Truck Stop", "category": "truck_stop", "lat": 37.74891, "lng": -122.19173,
     "tags": ["Truck access", "Diesel"], "address": "8255 San Leandro St, Oakland"},
    {"id": "ts-valleyoil-mv", "name": "Valley Oil", "category": "truck_stop", "lat": 37.3815, "lng": -122.06982,
     "tags": ["Diesel", "Truck access"], "address": "785 Yuba Dr, Mountain View"},
    {"id": "ts-western-sanleandro", "name": "Western States Oil", "category": "truck_stop", "lat": 37.70687, "lng": -122.1594,
     "tags": ["Diesel", "Truck access"], "address": "2709 Teagarden St, San Leandro"},
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
    {"id": "ra-hunterhill", "name": "Hunter Hill Rest Area", "category": "rest_area", "lat": 38.1516, "lng": -122.2172,
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
    return [_with_distance(raw, lat, lng) for raw in MOCK_PLACES]


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
        if category in DATA_ONLY:
            return search_data(category, lat, lng, "data")
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
        from services import tpims
        tpims.attach(found)
        return PlacesResponse(category=category, radius_km=radius, widened=widened, places=found, source=self.source)

    async def get(self, place_id: str, lat: float, lng: float) -> Optional[Place]:
        hit = next((p for p in self._all(lat, lng) if p.id == place_id), None)
        if hit:
            return hit
        for category in DATA_ONLY:
            raw = next((r for r in data_rows(category) if r.get("id") == place_id), None)
            if raw:
                return _with_distance(raw, lat, lng)
        return None


class OwnPlacesProvider:
    """Places from backend/data/places/<category>.json (scripts/build_places.py). Coordinates are never moved: the
    satellite parking check looks exactly there. A category without a file answers with an honest "no data"."""

    source = "own"

    def __init__(self, directory: Path = PLACES_DIR) -> None:
        self._dir = directory
        self._rows: dict[str, list[dict]] = {}
        self._by_id: Optional[dict[str, dict]] = None

    def rows(self, category: str) -> list[dict]:
        if category not in self._rows:
            path = self._dir / f"{category}.json"
            rows: list[dict] = []
            try:
                loaded = json.loads(path.read_text()) if path.exists() else []
                rows = [r for r in loaded if isinstance(r, dict)] if isinstance(loaded, list) else []
            except (OSError, ValueError) as e:
                logger.warning("own places %s unreadable: %s", path.name, e)
            self._rows[category] = rows
        return self._rows[category]

    async def search(self, category: Category, lat: float, lng: float) -> PlacesResponse:
        if category in DATA_ONLY:
            return search_data(category, lat, lng, "data")
        rows = self.rows(category)
        if not rows:
            return PlacesResponse(category=category, radius_km=0, widened=False, places=[], source=self.source,
                                  message=NO_DATA.format(what=category.replace("_", " ")))
        found: List[Place] = []
        radius, widened = OWN_RADII_KM[0], False
        for i, radius in enumerate(OWN_RADII_KM):
            dlat = radius / 111.0
            dlng = radius / max(1.0, 111.0 * math.cos(math.radians(lat)))
            box = [r for r in rows if abs(r["lat"] - lat) <= dlat and abs(r["lng"] - lng) <= dlng]
            found = sorted((p for p in (_with_distance(r, lat, lng) for r in box) if (p.distance_km or 0) <= radius),
                           key=lambda p: p.distance_km or 0)
            widened = i > 0
            if len(found) >= MIN_RESULTS:
                break
        found = found[:OWN_MAX]
        from services import tpims
        tpims.attach(found)
        return PlacesResponse(category=category, radius_km=radius, widened=widened, places=found, source=self.source)

    async def search_named(self, category: Category, match: Callable[[str], bool], lat: float, lng: float,
                           max_km: float = NAMED_MAX_KM) -> List[Place]:
        """The nearest places of a category whose name passes `match`, up to `max_km` away. "Find a Love's" wants
        the chain, and the nearest Love's is often past the ten closest truck stops a plain search returns
        (synthetic voice run 2026-11-08: Sacramento got Harbor Truck and Auto Plaza, the Love's is in Lodi)."""
        rows = [r for r in self.rows(category) if match(str(r.get("name") or ""))]
        found = sorted((p for p in (_with_distance(r, lat, lng) for r in rows) if (p.distance_km or 0) <= max_km),
                       key=lambda p: p.distance_km or 0)[:OWN_MAX]
        from services import tpims
        tpims.attach(found)
        return found

    async def get(self, place_id: str, lat: float, lng: float) -> Optional[Place]:
        if self._by_id is None:
            self._by_id = {}
            for f in sorted(self._dir.glob("*.json")) if self._dir.exists() else []:
                for r in self.rows(f.stem):
                    self._by_id[r["id"]] = r
        raw = self._by_id.get(place_id)
        if raw is None:
            for category in DATA_ONLY:
                raw = next((r for r in data_rows(category) if r.get("id") == place_id), None)
                if raw:
                    break
        return _with_distance(raw, lat, lng) if raw else None


_mock: PlacesProvider = MockPlacesProvider()
_google: Optional[PlacesProvider] = None
_own: Optional[PlacesProvider] = None


def get_places_provider() -> PlacesProvider:
    """PLACES_PROVIDER=own|google|mock picks one; unset keeps the old rule: Google when GOOGLE_MAPS_API_KEY is set,
    otherwise demo places."""
    global _google, _own
    choice = (os.environ.get("PLACES_PROVIDER") or "").strip().lower()
    if choice == "own":
        if _own is None:
            _own = OwnPlacesProvider()
        return _own
    if choice == "mock" or not os.environ.get("GOOGLE_MAPS_API_KEY"):
        return _mock
    if _google is None:
        from services.google_places import GooglePlacesProvider
        _google = GooglePlacesProvider()
    return _google
