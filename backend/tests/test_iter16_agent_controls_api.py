"""Iteration 16: /api/agent control intent (LLM gpt-5.4-mini) end-to-end via public URL.
Checks action types (phrasing varies). One retry per call for LLM flakiness."""
import os
import pytest
import requests


def _base():
    url = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")
    if not url:
        for line in open("/app/frontend/.env"):
            if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
                url = line.split("=", 1)[1].strip().strip('"')
    return url.rstrip("/")


BASE = _base()
LAT, LNG = 38.5816, -121.4944
PLACE = {"id": "geo-reno", "name": "Reno, NV", "category": "destination", "lat": 39.5296, "lng": -119.8138}
NOT_UNDERSTOOD = "didn't catch"


def call(text, active=False, speak=False, truck=None, **ui):
    body = {"text": text, "lat": LAT, "lng": LNG, "ui": {"driving": False, "voice_guidance": True,
                                                          "hands_free": True, "options_count": 0, **ui}}
    if active:
        body["active_place"] = PLACE
    if truck:
        body["truck_profile"] = truck
    last = None
    for _ in range(2):
        r = requests.post(f"{BASE}/api/agent" + ("?speak=true" if speak else ""), json=body, timeout=90)
        assert r.status_code == 200, r.text
        last = r.json()
        if NOT_UNDERSTOOD not in (last.get("reply") or ""):
            return last
    return last


def acts(d):
    return [(a["type"], a.get("value")) for a in d.get("actions") or []]


def types(d):
    return [a[0] for a in acts(d)]


# --- trip control
class TestTrip:
    def test_lets_go_start_ride_with_route(self):
        d = call("Okay, let's go. Can you start the ride?", active=True)
        assert d["status"] == "chat" and "start_navigation" in types(d), d
        assert NOT_UNDERSTOOD not in d["reply"]

    def test_lets_go_speak_true(self):
        d = call("Okay, let's go. Can you start the ride?", active=True, speak=True)
        assert "start_navigation" in types(d), d

    def test_start_without_route(self):
        d = call("Start the ride", active=False)
        assert "start_navigation" not in types(d) and NOT_UNDERSTOOD not in d["reply"], d

    def test_already_driving(self):
        d = call("Let's go, start driving", active=True, driving=True)
        assert "start_navigation" not in types(d), d

    def test_end_route(self):
        d = call("End the route", active=True, driving=True)
        assert "end_route" in types(d), d

    def test_show_options(self):
        d = call("Show me the other options", active=True, options_count=3)
        assert "show_options" in types(d), d

    def test_show_options_none(self):
        d = call("Show me the other options", active=True, options_count=0)
        assert "show_options" not in types(d), d


# --- map view
class TestMap:
    @pytest.mark.parametrize("text,kind", [("zoom out", "zoom_out"), ("zoom in", "zoom_in"),
                                           ("show the whole route", "overview"), ("recenter the map on me", "recenter")])
    def test_map(self, text, kind):
        d = call(text, active=True, driving=True)
        assert kind in types(d), d


# --- sound
class TestSound:
    def test_mute_directions(self):
        d = call("mute the directions", active=True, driving=True)
        assert ("voice_guidance", False) in acts(d), d

    def test_wake_word_off(self):
        d = call("stop listening for hey buddy", active=False)
        assert ("wake_word", False) in acts(d), d

    def test_stop_talking(self):
        d = call("be quiet", active=True, driving=True)
        assert "stop_talking" in types(d) and d["end_conversation"] is True, d


# --- truck data and driver prefs
class TestTruckPrefs:
    def test_height_weight_with_route_reroutes(self):
        d = call("my truck is 13 foot 2 and 70000 pounds", active=True)
        a = acts(d)
        assert ("set_height_in", 158) in a and ("set_weight_lb", 70000) in a and "reroute" in types(d), d

    def test_hazmat_trailer_no_route(self):
        d = call("I'm hauling hazmat now and the trailer is 48 feet", active=False)
        a = acts(d)
        assert ("set_hazmat", True) in a and ("set_trailer_ft", 48) in a and "reroute" not in types(d), d

    def test_height_out_of_range(self):
        d = call("set my height to 25 feet", active=False)
        assert "set_height_in" not in types(d) and "between" in d["reply"].lower(), d

    def test_name_tolls_ferries(self):
        d = call("Call me Mike, avoid tolls and avoid ferries", active=False)
        a = acts(d)
        assert ("set_driver_name", "Mike") in a and ("avoid_tolls", True) in a and ("avoid_ferries_tunnels", True) in a, d

    def test_favorites_and_break(self):
        d = call("add Love's to my favorites and remind me to take a break every 4 hours",
                 truck={"favorite_chains": ["Pilot"]})
        a = acts(d)
        assert any(t == "add_favorite_chain" and "love" in str(v).lower() for t, v in a) and ("set_break_hours", 4) in a, d

    def test_remove_favorite_and_breaks_off(self):
        d = call("remove Pilot from my favorites and turn off break reminders", truck={"favorite_chains": ["Pilot"]})
        a = acts(d)
        assert ("remove_favorite_chain", "Pilot") in a and ("set_break_hours", None) in a, d


# --- regression: find/go still route
class TestRegression:
    def test_fuel(self):
        d = call("I need fuel")
        assert d["status"] == "route_set" and d["place"], d

    def test_go_reno(self):
        d = call("take me to Reno")
        assert d["status"] == "route_set" and "Reno" in d["place"]["name"], d
