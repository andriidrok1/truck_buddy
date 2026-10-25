"""Truck Buddy: agent options list (Google Places), on-demand parking check, route by place object."""
import os
import re

import pytest
import requests


def _base():
    url = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")
    if not url:
        env = open("/app/frontend/.env").read()
        url = re.search(r"EXPO_PUBLIC_BACKEND_URL=(\S+)", env).group(1)
    return url.strip().strip('"').rstrip("/")


BASE_URL = _base()
LAT, LNG = 38.5816, -121.4944
_cache = {}


@pytest.fixture(scope="module")
def s():
    x = requests.Session()
    x.headers.update({"Content-Type": "application/json"})
    return x


def agent(s, text):
    if text not in _cache:
        r = s.post(f"{BASE_URL}/api/agent", json={"text": text, "lat": LAT, "lng": LNG}, timeout=60)
        assert r.status_code == 200, r.text
        _cache[text] = r.json()
    return _cache[text]


# Health: places provider (google only when GOOGLE_MAPS_API_KEY set, else mock)
def test_health_providers(s):
    r = s.get(f"{BASE_URL}/api/health", timeout=15)
    assert r.status_code == 200
    assert r.json()["providers"]["places"] in ("google", "mock")


# Agent (merged behavior): hotel -> route_set to best place + other options
def test_agent_hotel_route_set_with_options(s):
    d = agent(s, "find me a hotel where I can park my rig")
    assert d["status"] == "route_set", d
    assert d["category"] == "hotel"
    assert d["place"] and d["place"]["category"] == "hotel"
    assert d["route"] and d["route"]["distance_mi"] > 0
    assert d["parking"] is None or d["parking"]["verdict"] in ("listed", "likely", "unlikely", "unclear")
    opts = d["options"]
    assert len(opts) <= 4
    assert all(o["id"] != d["place"]["id"] for o in opts)
    for o in opts:
        assert o["id"] and o["name"] and o["category"] == "hotel"
        assert isinstance(o["distance_mi"], (int, float))
    assert d["place"]["name"].split()[0].lower() in d["reply"].lower()


# Agent: truck stop / rest area categories -> route_set
@pytest.mark.parametrize("text,cat", [("I need a truck stop", "truck_stop"), ("Find a rest area", "rest_area")])
def test_agent_categories(s, text, cat):
    d = agent(s, text)
    assert d["status"] in ("route_set", "no_results"), d
    assert d["category"] == cat
    if d["status"] == "route_set":
        assert d["place"]["category"] == cat
        assert all(o["category"] == cat for o in d["options"])


# Parking: on-demand check from option object
def test_parking_for_option(s):
    d = agent(s, "find me a hotel where I can park my rig")
    opt = (d["options"] or [d["place"]])[0]
    r = s.post(f"{BASE_URL}/api/parking", json={"place": opt}, timeout=90)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["verdict"] in ("listed", "likely", "unlikely", "unclear")
    assert p["label"] and p["source"] in ("place_data", "satellite", "none")
    if p["source"] == "satellite":
        assert p["image_path"] and p["image_path"].startswith("/satellite?")
        img = s.get(f"{BASE_URL}/api{p['image_path']}", timeout=30)
        assert img.status_code == 200 and img.headers["content-type"].startswith("image/")


def test_parking_validation(s):
    r = s.post(f"{BASE_URL}/api/parking", json={"place": {"id": "x"}}, timeout=15)
    assert r.status_code == 422


# Route: by place object, by previously seen place_id, unknown id -> 404
def test_route_with_place_object(s):
    d = agent(s, "find me a hotel where I can park my rig")
    opt = (d["options"] or [d["place"]])[0]
    r = s.post(f"{BASE_URL}/api/route", json={"lat": LAT, "lng": LNG, "place": opt}, timeout=90)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["place"]["id"] == opt["id"]
    assert len(d["route"]["geometry"]) > 2 and d["route"]["summary"]
    assert d["parking"]["verdict"] in ("listed", "likely", "unlikely", "unclear")


def test_route_legacy_place_id(s):
    opt = agent(s, "find me a hotel where I can park my rig")["options"][-1]
    r = s.post(f"{BASE_URL}/api/route", json={"lat": LAT, "lng": LNG, "place_id": opt["id"]}, timeout=90)
    assert r.status_code == 200, r.text
    assert r.json()["place"]["name"] == opt["name"]


def test_route_unknown_id_404(s):
    r = s.post(f"{BASE_URL}/api/route", json={"lat": LAT, "lng": LNG, "place_id": "TEST_nope"}, timeout=15)
    assert r.status_code == 404


def test_route_missing_place_404(s):
    r = s.post(f"{BASE_URL}/api/route", json={"lat": LAT, "lng": LNG}, timeout=15)
    assert r.status_code == 404


# Agent: not understood (no Google call)
def test_agent_not_understood(s):
    r = s.post(f"{BASE_URL}/api/agent", json={"text": "?? 1", "lat": LAT, "lng": LNG}, timeout=15)
    assert r.status_code == 200
    assert r.json()["status"] == "not_understood"
