"""Demo places: real coordinates (never shifted), coverage from SF and Sacramento, NO_PLACES elsewhere."""
import asyncio

from services.geo import haversine_km
from services.places import MOCK_PLACES, NO_PLACES, MockPlacesProvider

SF = (37.7749, -122.4194)
SAC = (38.5816, -121.4944)


def _search(cat, at):
    return asyncio.run(MockPlacesProvider().search(cat, *at))


def test_three_truck_stops_within_60km_of_sf_and_sacramento():
    for at in (SF, SAC):
        r = _search("truck_stop", at)
        near = [p for p in r.places if haversine_km(*at, p.lat, p.lng) <= 60]
        assert len(near) >= 3, (at, [p.name for p in r.places])


def test_hotels_and_rest_areas_found_in_both_regions():
    for at in (SF, SAC):
        for cat in ("hotel", "rest_area"):
            assert _search(cat, at).places, (at, cat)


def test_coordinates_never_shifted():
    raw = {p["id"]: (p["lat"], p["lng"]) for p in MOCK_PLACES}
    for at in (SF, SAC, (38.25, -122.05)):
        for cat in ("truck_stop", "hotel", "rest_area"):
            for p in _search(cat, at).places:
                assert (p.lat, p.lng) == raw[p.id]


def test_outside_coverage_returns_no_places():
    for at in ((34.05, -118.24), (32.78, -96.80)):  # Los Angeles, Dallas
        r = _search("truck_stop", at)
        assert r.places == [] and r.message == NO_PLACES
