"""TruckBuddy backend API tests - MOCK MVP."""
import os
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


# Health
def test_health(s):
    r = s.get(f"{API}/health", timeout=60)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert data["providers"]["places"] == "mock"
    assert data["providers"]["agent"] == "gpt-5.4-mini"  # single mini model since iter7


# OpenAPI
def test_openapi(s):
    r = s.get(f"{API}/openapi.json", timeout=60)
    assert r.status_code == 200
    assert "paths" in r.json()


# Places
@pytest.mark.parametrize("cat", ["truck_stop", "hotel", "rest_area"])
def test_places_category(s, cat):
    r = s.get(f"{API}/places", params={"category": cat, "lat": 38.5816, "lng": -121.4944}, timeout=60)
    assert r.status_code == 200
    data = r.json()
    assert data["category"] == cat
    assert len(data["places"]) > 0
    # all match category
    assert all(p["category"] == cat for p in data["places"])
    # sorted by distance
    dists = [p["distance_km"] for p in data["places"]]
    assert dists == sorted(dists)


# Agent intents
def test_agent_truck_stop(s):
    r = s.post(f"{API}/agent", json={"text": "truck stop", "lat": 38.5816, "lng": -121.4944}, timeout=60)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "route_set"
    assert d["category"] == "truck_stop"
    assert d["place"] is not None
    assert d["route"]["duration_min"] > 0


def test_agent_rest_area(s):
    r = s.post(f"{API}/agent", json={"text": "find a rest area", "lat": 38.5816, "lng": -121.4944}, timeout=60)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "route_set"
    assert d["category"] == "rest_area"


def test_agent_hotel(s):
    r = s.post(f"{API}/agent", json={"text": "I'm tired find me a hotel", "lat": 38.5816, "lng": -121.4944}, timeout=60)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "route_set"
    assert d["category"] == "hotel"


def test_agent_empty(s):
    r = s.post(f"{API}/agent", json={"text": "", "lat": 38.5816, "lng": -121.4944}, timeout=60)
    assert r.status_code == 200
    assert r.json()["status"] == "not_understood"


# Route
def test_route_valid(s):
    r = s.post(f"{API}/route", json={"lat": 38.5816, "lng": -121.4944, "place_id": "ts-49er"}, timeout=60)
    assert r.status_code == 200
    d = r.json()
    assert d["place"]["id"] == "ts-49er"
    assert "geometry" in d["route"]
    assert d["route"]["distance_mi"] > 0


def test_route_invalid(s):
    r = s.post(f"{API}/route", json={"lat": 38.5816, "lng": -121.4944, "place_id": "nope-xyz"}, timeout=60)
    assert r.status_code == 404
