# Iteration 11: unit tests for the text-for-speech layer (spoken) and conversational route replies.
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.speech_text import spoken  # noqa: E402
from services.agent import _route_reply, TEMPLATES  # noqa: E402
from models import Place, RouteInfo  # noqa: E402


# spoken(): road names, abbreviations, numbers, punctuation
@pytest.mark.parametrize("raw,expected", [
    ("I-80 Exit 85, 4.7 miles", "Interstate 80 Exit 85, about four and a half miles"),
    ("US-50 & Howe Ave", "U.S. 50 and Howe Avenue"),
    ("SR-99", "State Route 99"),
    ("CA-99", "State Route 99"),
    ("W. Main St", "West Main Street"),
    ("N. Market Blvd", "North Market Boulevard"),
    ("S. Watt Ave", "South Watt Avenue"),
    ("E. Elm Rd", "East Elm Road"),
    ("Fair Oaks Pkwy", "Fair Oaks Parkway"),
    ("Sunrise Dr", "Sunrise Drive"),
    ("Hwy 50", "Highway 50"),
    ("2 h 49 min", "two hours and fifty minutes"),
    ("12.4 miles", "about twelve miles"),
    ("13 minutes", "about fifteen minutes"),
    ("DEF at the pump", "D-E-F at the pump"),
    ("4.5 miles", "four and a half miles"),
    ("1 mile", "one mile"),
    ("10 miles", "ten miles"),
    ("1 h 58 min", "two hours"),
    ("15 min", "fifteen minutes"),
])
def test_spoken_examples(raw, expected):
    assert spoken(raw) == expected


def test_spoken_us_not_u_south():
    out = spoken("Take US-50 & Howe Ave, 3.2 mi")
    assert "U.South" not in out and "U.S. 50" in out


def test_spoken_removes_symbols():
    out = spoken("Pilot (Travel Center) · showers/fuel [24h]")
    for ch in "·/()[]":
        assert ch not in out, out


def test_spoken_route_summary():
    assert spoken("5 h 20 min · 312 mi") == "five hours and twenty minutes, three hundred twelve miles"


# Edge cases likely in real addresses / place names (known issues reported in iteration 11)
@pytest.mark.parametrize("raw,bad", [
    ("Route to St. Louis is set", "Street Louis"),
    ("Open 24/7 with showers", "24 or 7"),
])
def test_spoken_edge_cases(raw, bad):
    assert bad not in spoken(raw), spoken(raw)


# _route_reply(): conversational notice, templates rotation, driver name every third
def _place(cat="truck_stop"):
    return Place(id="p1", name="Pilot Travel Center", category=cat, lat=38.6, lng=-121.4,
                 address="1234 W. Main St, Sacramento, CA", distance_mi=4.7)


def _route(notices=None):
    return RouteInfo(geometry=[[38.6, -121.4]], distance_m=7560, duration_s=600, distance_mi=4.7,
                     duration_min=10, summary="10 min · 4.7 mi", source="here", notices=notices or [])


def test_route_reply_toll_notice():
    out = _route_reply(_place(), _route(["Tolls couldn't be fully avoided on this route."]), 0)
    assert out.endswith("Heads up, couldn't dodge all the tolls on this one."), out


def test_route_reply_rotation_and_name():
    replies = [_route_reply(_place(), _route(), n, "Mike Johnson") for n in range(6)]
    assert len(set(replies)) >= 4
    with_name = [i for i, r in enumerate(replies) if "Mike" in r]
    assert with_name == [2, 5], with_name
    assert "Johnson" not in " ".join(replies)
    assert "right off W. Main St" in replies[0] or "just up W. Main St" in replies[1]


def test_template_counts():
    assert all(len(TEMPLATES[c]) == 4 for c in ("truck_stop", "hotel", "rest_area"))
    assert len(TEMPLATES["destination"]) == 3
