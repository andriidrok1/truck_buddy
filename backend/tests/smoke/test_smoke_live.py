"""Nightly smoke against the deployed backend: is it up, routing with HERE, answering, speaking.

Needs EXPO_PUBLIC_BACKEND_URL and ACCESS_CODE (tests/conftest.py adds the token to every call).
Four calls, under a minute, no voice upload. Never part of the PR gate.
"""
import os

import pytest
import requests

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
SAC = {"lat": 38.5816, "lng": -121.4944}

pytestmark = pytest.mark.smoke


def test_health_reports_real_providers():
    r = requests.get(f"{BASE}/api/health", timeout=30)
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    providers = body.get("providers") or body
    assert providers.get("routing") == "here", providers
    assert providers.get("places") in ("google", "mock"), providers


def test_route_to_a_truck_stop_comes_from_here():
    s = requests.Session()
    places = s.get(f"{BASE}/api/places", params={"category": "truck_stop", **SAC}, timeout=60).json()
    assert places["places"], places
    place = places["places"][0]
    r = s.post(f"{BASE}/api/route", json={**SAC, "place": place, "skip_parking": True}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    route = r.json()["route"]
    assert route["source"] == "here"
    assert len(route["geometry"]) >= 2 and route["maneuvers"]


def test_agent_answers_a_find():
    r = requests.post(f"{BASE}/api/agent", json={"text": "find me the nearest truck stop", **SAC}, timeout=120)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["status"] in ("route_set", "options"), body.get("status")
    assert body.get("reply")


def test_speak_returns_audio():
    r = requests.post(f"{BASE}/api/speak", json={"text": "Smoke test."}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    a = requests.get(f"{BASE}{r.json()['audio_url']}", timeout=60)
    assert a.status_code == 200 and len(a.content) > 1000
