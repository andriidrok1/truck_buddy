"""TruckBuddy merge (iteration 2): voice -> route_set + parking tips + options; /api/parking; /api/satellite."""
import json
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
VERDICTS = ("listed", "likely", "unlikely", "unclear")
_cache = {}


@pytest.fixture(scope="module")
def s():
    return requests.Session()


def _check_parking(p):
    assert p["verdict"] in VERDICTS
    assert p["label"] and isinstance(p["reason"], str)
    assert p["source"] in ("place_data", "satellite", "none")
    for sp in p["spots"]:
        assert sp["kind"] in ("entry", "park")
        assert 0 <= sp["x"] <= 1 and 0 <= sp["y"] <= 1
        assert abs(sp["lat"]) <= 90 and abs(sp["lng"]) <= 180


# Voice: real speech mp3 -> route_set with place, route, parking, options, audio
def test_voice_fuel_route_set(s):
    if not os.path.exists("/tmp/q.mp3"):
        pytest.skip("/tmp/q.mp3 missing")
    with open("/tmp/q.mp3", "rb") as f:
        r = s.post(f"{BASE_URL}/api/voice", files={"audio": ("q.mp3", f, "audio/mpeg")},
                   data={"lat": LAT, "lng": LNG}, timeout=120)
    assert r.status_code == 200, r.text
    d = r.json()
    _cache["voice"] = d
    print("VOICE:", json.dumps({k: d[k] for k in ("status", "transcript", "reply")}), d["parking"])
    assert "fuel" in d["transcript"].lower()
    assert d["status"] == "route_set", d
    assert d["category"] == "truck_stop"
    assert d["place"] and d["route"] and d["route"]["distance_mi"] > 0
    _check_parking(d["parking"])
    assert d["parking"]["image_path"].startswith("/satellite?")
    assert isinstance(d["options"], list) and len(d["options"]) <= 4
    assert all(o["id"] != d["place"]["id"] for o in d["options"])
    assert d["audio_url"] and d["audio_url"].startswith("/api/voice/audio/")
    a = s.get(f"{BASE_URL}{d['audio_url']}", timeout=30)
    assert a.status_code == 200 and a.headers["content-type"] == "audio/mpeg" and len(a.content) > 1000


# Agent JSON: hotel / vague phrases -> route_set
@pytest.mark.parametrize("text,cat", [("I'm tired, need a bed for the night", "hotel"),
                                      ("I need to stretch my legs", "rest_area")])
def test_agent_route_set(s, text, cat):
    r = s.post(f"{BASE_URL}/api/agent", json={"text": text, "lat": LAT, "lng": LNG}, timeout=120)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "route_set", d
    assert d["category"] == cat and d["place"]["category"] == cat
    _check_parking(d["parking"])
    _cache[cat] = d


# Follow-up with active_place -> chat, no tool call
def test_agent_how_far_chat(s):
    place = (_cache.get("hotel") or {}).get("place")
    if not place:
        pytest.skip("no hotel place")
    r = s.post(f"{BASE_URL}/api/agent", json={"text": "how far is it?", "lat": LAT, "lng": LNG,
                                              "active_place": place}, timeout=60)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "chat", d
    assert re.search(r"\d", d["reply"]) and d["place"] is None


def test_agent_gibberish(s):
    r = s.post(f"{BASE_URL}/api/agent", json={"text": "asdkjh qwpoe zzxv", "lat": LAT, "lng": LNG}, timeout=60)
    assert r.status_code == 200
    assert r.json()["status"] == "not_understood"


def test_agent_bad_lat_422(s):
    r = s.post(f"{BASE_URL}/api/agent", json={"text": "fuel", "lat": 999, "lng": LNG}, timeout=15)
    assert r.status_code == 422


# Parking: unlisted place at given coords -> verdict + tips + spots
def test_parking_basic(s):
    place = {"id": "TEST_p1", "name": "TEST lot", "category": "truck_stop", "lat": 38.5935, "lng": -121.43, "tags": []}
    r = s.post(f"{BASE_URL}/api/parking", json={"place": place}, timeout=90)
    assert r.status_code == 200, r.text
    p = r.json()
    print("PARKING basic:", p)
    _check_parking(p)
    assert p["source"] == "satellite"
    assert p["verdict"] == "likely", p
    assert p["entry_tip"] and p["park_tip"]
    assert len(p["spots"]) >= 1


# Parking: listed in data but imagery shows dense Manhattan blocks -> unclear + specific label
def test_parking_listed_but_not_visible(s):
    place = {"id": "TEST_p2", "name": "TEST listed", "category": "truck_stop", "lat": 40.75800, "lng": -73.98550,
             "tags": ["Truck parking"]}
    r = s.post(f"{BASE_URL}/api/parking", json={"place": place}, timeout=90)
    assert r.status_code == 200, r.text
    p = r.json()
    print("PARKING listed/water:", p)
    assert p["verdict"] == "unclear", p
    assert p["label"] == "Truck parking listed, not visible on satellite"


def test_satellite_jpeg(s):
    r = s.get(f"{BASE_URL}/api/satellite", params={"lat": 38.5935, "lng": -121.43}, timeout=30)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"


def test_satellite_bad_lat_422(s):
    r = s.get(f"{BASE_URL}/api/satellite", params={"lat": 999, "lng": 0}, timeout=15)
    assert r.status_code == 422
