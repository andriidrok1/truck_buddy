"""Geometry side of the entrance, landmark-only rewording, /route not waiting, geocoder bias."""
import asyncio
import math

import pytest

from models import Approach, ParkingCheck, ParkingSpot, Place
from services import approach, parking

# Petro Stopping Center, Corning: truck entrance on the north side of South Ave (E-W road).
E = (39.90650, -122.19570)
ROAD_LAT = 39.90610  # South Ave, ~45 m south of E


def ap_at(bearing: int, back_m: float = 100.0) -> Approach:
    """Approach point on South Ave, 100 m before E's longitude, for a heading."""
    b = math.radians(bearing)
    dlng = -math.sin(b) * back_m / (111320 * math.cos(math.radians(ROAD_LAT)))
    return Approach(bearing=bearing, compass="x", street="South Ave", lat=ROAD_LAT, lng=E[1] + dlng)


def test_known_example_heading_north():
    # Heading north, E due east of you -> right; due west -> left.
    a = Approach(bearing=0, compass="south", lat=38.0, lng=-121.0)
    assert approach.side(a, 38.0005, -120.999) == "right"
    assert approach.side(a, 38.0005, -121.001) == "left"


def test_petro_corning_same_side_for_close_headings():
    assert approach.side(ap_at(87), *E) == approach.side(ap_at(97), *E) == "left"  # eastbound: north side is left


def test_petro_corning_mirror_approach_flips():
    assert approach.side(ap_at(267), *E) == "right"


def test_dead_ahead_names_no_side():
    a = Approach(bearing=0, compass="south", lat=38.0, lng=-121.0)
    assert approach.side(a, 38.001, -121.0) is None


def test_clean_drops_directional_words():
    assert approach._clean("Just after the landscaped island") == "just after the landscaped island"
    out = approach._clean("East driveway, right after the fuel canopy, southwest corner")
    for w in ("east", "right", "southwest"):
        assert w not in out.lower()
    assert "fuel canopy" in out


def _check(entry=True):
    spots = [ParkingSpot(kind="entry", x=0.5, y=0.4, lat=E[0], lng=E[1])] if entry else []
    return ParkingCheck(verdict="likely", label="", reason="", source="satellite", image_path="/s",
                        entry_tip="Use the wide north driveway, just after the landscaped island", spots=spots)


def test_relative_tip_format_without_vision(monkeypatch):
    async def fake_landmarks(tip):
        return "just after the landscaped island"
    monkeypatch.setattr(approach, "landmarks", fake_landmarks)
    p = Place(id="petro", name="Petro", category="truck_stop", lat=39.90611, lng=-122.19623)
    res = asyncio.run(approach.relative(p, _check(), ap_at(87)))
    assert res.relative and res.entry_tip == "On your left, off South Ave, just after the landscaped island"


def test_no_entry_point_keeps_base_tip(monkeypatch):
    async def fake_landmarks(tip):
        raise AssertionError("not needed without E")
    monkeypatch.setattr(approach, "landmarks", fake_landmarks)
    p = Place(id="petro2", name="Petro", category="truck_stop", lat=39.9, lng=-122.2)
    base = _check(entry=False)
    assert asyncio.run(approach.relative(p, base, ap_at(87))) is base


def test_best_uses_cached_landmarks():
    p = Place(id="petro3", name="Petro", category="truck_stop", lat=39.80611, lng=-122.19623)
    parking._verdict_cache[parking._key(p.lat, p.lng)] = _check()
    approach.remember(p, ap_at(267))
    assert not approach.best(p).relative  # landmarks not computed yet
    approach._landmarks.put(_check().entry_tip, "just after the landscaped island")
    assert approach.best(p).entry_tip == "On your right, off South Ave, just after the landscaped island"


@pytest.mark.parametrize("q", ["Pilot, 3740 Ramos Rd, Williams"])
def test_geocoder_stays_in_california(q):
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    from services.geocode import geocode
    r = asyncio.run(geocode(q, 38.5816, -121.4944))
    assert r and ", CA" in (r[0].address or r[0].name) and r[0].distance_mi < 150
