# Iteration 28 review (T1): town parking line, "take the second one", control phrases with a route, find-near follow-up
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"
SAC = {"lat": 38.58, "lng": -121.49}
BAD = ("unlikely", "tight", "can't find", "didn't catch")


def _ask(text, sid, active=None, **extra):
    body = {"text": text, "session_id": sid, **SAC, **extra}
    if active:
        body["active_place"] = active
    r = requests.post(f"{API}/agent", json=body, timeout=90)
    assert r.status_code == 200, r.text
    return r.json()


def _sid(tag):
    return f"T1-{tag}-{uuid.uuid4().hex[:6]}"


# Town destination: parking question gets the town line, never a verdict
def test_town_parking_line():
    sid = _sid("town")
    r = _ask("take me to Sacramento", sid)
    assert r["status"] == "route_set", r
    assert r["place"]["id"].startswith("here-town"), r["place"]["id"]
    p = _ask("what's the parking like there", sid, active=r["place"])
    low = p["reply"].lower()
    assert "town" in low and "unlikely" not in low and "tight" not in low, p["reply"]


# Dialog survives route_set: "take the second one" -> options[0] of the first response
def test_take_the_second_one():
    sid = _sid("second")
    r = _ask("I need fuel", sid)
    assert r["status"] == "route_set" and r["options"], r
    want = r["options"][0]["id"]
    s = _ask("take the second one", sid, active=r["place"])
    assert s["status"] == "route_set", s
    assert s["place"]["id"] == want, (s["place"]["name"], r["options"][0]["name"])


# Control phrases with an active route -> start_navigation, never geocoded
@pytest.mark.parametrize("text", ["start the ride", "the stars are out", "let's go"])
def test_controls_with_route(text):
    sid = _sid("ctl")
    r = _ask("I need fuel", sid)
    assert r["status"] == "route_set", r
    c = _ask(text, sid, active=r["place"], route_active=True)
    kinds = [a["type"] for a in c.get("actions") or []]
    assert "start_navigation" in kinds, (text, c)
    assert "can't find" not in c["reply"], c["reply"]


# "and a hotel there" after a find-near reuses the point (repeat to catch LLM flakiness)
@pytest.mark.parametrize("run", range(3))
def test_hotel_there_reuses_point(run):
    sid = _sid("near")
    r = _ask("where can I park close to my delivery in Stockton", sid)
    assert r["status"] == "route_set", r
    h = _ask("and a hotel there", sid, active=r["place"])
    assert h["status"] == "route_set" and h["place"]["category"] == "hotel", h
    assert abs(h["place"]["lat"] - 37.96) < 0.4, (h["place"]["name"], h["place"]["lat"])
