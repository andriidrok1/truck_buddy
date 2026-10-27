# Truck routing feature: "take me to <address>" agent, truck_profile validation, /api/route, /api/voice, fallbacks
import asyncio
import json
import os
import sys

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
SAC = {"lat": 38.5816, "lng": -121.4944}
DEFAULT_TP = {"height_in": 162, "trailer_ft": 53, "weight_lb": 80000, "hazmat": False}
sys.path.insert(0, "/app/backend")


@pytest.fixture(scope="module")
def s():
    x = requests.Session()
    x.headers.update({"Content-Type": "application/json"})
    return x


def _agent(s, text, tp=None, **kw):
    body = {"text": text, **SAC, "truck_profile": tp or DEFAULT_TP, **kw}
    return s.post(f"{BASE_URL}/api/agent", json=body, timeout=120)


# --- Agent: destination routing ---
class TestAgentDestination:
    def test_take_me_to_reno(self, s):
        r = _agent(s, "take me to Reno, Nevada")
        assert r.status_code == 200, r.text
        d = r.json()
        print("REPLY:", d["reply"], "| SUMMARY:", d["route"] and d["route"]["summary"], d["route"] and d["route"]["notices"])
        assert d["status"] == "route_set"
        assert d["place"]["category"] == "destination"
        assert "reno" in (d["place"]["address"] or d["place"]["name"]).lower()
        assert d["route"]["source"] == "here"
        assert d["route"]["truck_aware"] is True
        assert " h " in d["route"]["summary"] and "mi" in d["route"]["summary"]
        assert 100 < d["route"]["distance_mi"] < 180
        assert len(d["route"]["geometry"]) > 100
        assert d["parking"] is None or "verdict" in d["parking"]  # iter17: destinations checked too
        assert d["options"] == []

    def test_hazmat_low_profile_differs(self, s):
        tp = {"height_in": 170, "trailer_ft": 53, "weight_lb": 80000, "hazmat": True}
        r = _agent(s, "take me to Reno, Nevada", tp)
        assert r.status_code == 200, r.text
        d = r.json()
        print("HAZMAT SUMMARY:", d["route"]["summary"], d["route"]["notices"])
        assert d["status"] == "route_set"
        assert d["route"]["source"] == "here" and d["route"]["truck_aware"] is True


# --- truck_profile validation ---
class TestTruckProfileValidation:
    @pytest.mark.parametrize("bad", [
        {"height_in": 30}, {"height_in": 250}, {"trailer_ft": 5}, {"trailer_ft": 90},
        {"weight_lb": 100}, {"weight_lb": 999999}, {"hazmat": "maybe"}, {"height_in": "tall"},
    ])
    def test_invalid_agent_422(self, s, bad):
        r = s.post(f"{BASE_URL}/api/agent", json={"text": "take me to Reno", **SAC, "truck_profile": {**DEFAULT_TP, **bad}}, timeout=30)
        assert r.status_code == 422

    def test_invalid_route_422(self, s):
        place = {"id": "x", "name": "Reno", "category": "destination", "lat": 39.5296, "lng": -119.8138}
        r = s.post(f"{BASE_URL}/api/route", json={**SAC, "place": place, "truck_profile": {"height_in": 10}}, timeout=30)
        assert r.status_code == 422

    def test_old_shape_ignored_defaults(self, s):
        # legacy {height, trailer_length} strings -> extra keys ignored, defaults used
        place = {"id": "x", "name": "Reno", "category": "destination", "lat": 39.5296, "lng": -119.8138}
        r = s.post(f"{BASE_URL}/api/route", json={**SAC, "place": place, "truck_profile": {"height": "13'6", "trailer_length": "53"}}, timeout=90)
        assert r.status_code in (200, 422)


# --- /api/route with place + truck profile ---
class TestRouteEndpoint:
    def test_route_destination_here(self, s):
        place = {"id": "TEST_reno", "name": "Reno", "category": "destination", "lat": 39.5296, "lng": -119.8138}
        r = s.post(f"{BASE_URL}/api/route", json={**SAC, "place": place, "truck_profile": DEFAULT_TP}, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        print("ROUTE parking:", d.get("parking") and d["parking"]["verdict"])
        assert d["route"]["source"] == "here" and d["route"]["truck_aware"] is True
        assert len(d["route"]["geometry"]) > 100
        assert d["route"]["geometry"][0][0] == pytest.approx(38.58, abs=0.1)
        assert d["route"]["geometry"][-1][0] == pytest.approx(39.53, abs=0.1)


# --- /api/voice with truck_profile form field ---
class TestVoice:
    def test_voice_with_truck_profile(self):
        with open("/tmp/q.mp3", "rb") as f:
            r = requests.post(f"{BASE_URL}/api/voice", files={"audio": ("q.mp3", f, "audio/mpeg")},
                              data={"lat": SAC["lat"], "lng": SAC["lng"], "truck_profile": json.dumps({**DEFAULT_TP, "hazmat": True})},
                              timeout=180)
        assert r.status_code == 200, r.text
        d = r.json()
        print("VOICE:", d["transcript"], d["status"], d["reply"])
        assert d["status"] in ("route_set", "options", "chat")
        assert d["audio_url"]

    def test_voice_invalid_truck_profile(self):
        with open("/tmp/q.mp3", "rb") as f:
            r = requests.post(f"{BASE_URL}/api/voice", files={"audio": ("q.mp3", f, "audio/mpeg")},
                              data={"lat": SAC["lat"], "lng": SAC["lng"], "truck_profile": json.dumps({"height_in": 5})}, timeout=60)
        assert r.status_code == 422

    def test_voice_bad_json_truck_profile(self):
        with open("/tmp/q.mp3", "rb") as f:
            r = requests.post(f"{BASE_URL}/api/voice", files={"audio": ("q.mp3", f, "audio/mpeg")},
                              data={"lat": SAC["lat"], "lng": SAC["lng"], "truck_profile": "{not json"}, timeout=60)
        assert r.status_code == 422


# --- Existing stop search still works ---
class TestStopSearch:
    def test_need_fuel(self, s):
        r = _agent(s, "I need fuel")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "route_set"
        assert d["place"]["category"] == "truck_stop"
        assert d["parking"] is None or d["parking"]["verdict"] in ("listed", "likely", "unlikely", "unclear")
        assert len(d["options"]) >= 1


# --- Fallback providers called directly (ORS + geocoders) ---
class TestFallbacks:
    def _env(self):
        from dotenv import load_dotenv
        from services import http
        load_dotenv("/app/backend/.env")
        http._client = None  # each asyncio.run() has its own loop; the shared client is bound to the old one

    def test_ors_direct(self):
        self._env()
        from models import TruckProfile
        from services import routing
        r = asyncio.run(routing._ors(38.5816, -121.4944, 39.5296, -119.8138, TruckProfile()))
        print("ORS:", r.summary, len(r.geometry))
        assert r.source == "openrouteservice" and r.truck_aware and len(r.geometry) > 50

    def test_here_fail_falls_back_to_ors(self, monkeypatch):
        self._env()
        from models import TruckProfile
        from services import routing
        monkeypatch.setenv("HERE_API_KEY", "INVALID_KEY")
        r = asyncio.run(routing.get_routing_provider().route(38.5816, -121.4944, 39.5296, -119.8138, TruckProfile()))
        assert r.source == "openrouteservice"

    def test_both_fail_raises(self, monkeypatch):
        """Keys configured but every provider down: an error, never a made-up line the driver would follow."""
        self._env()
        from services import routing
        monkeypatch.setenv("HERE_API_KEY", "INVALID")
        monkeypatch.setenv("ORS_API_KEY", "INVALID")
        with pytest.raises(routing.RoutingUnavailable):
            asyncio.run(routing.get_routing_provider().route(38.5816, -121.4944, 39.5296, -119.8138))

    def test_no_keys_mock(self, monkeypatch):
        """No routing key at all (local demo): the curved mock line, marked as such."""
        from services import routing
        monkeypatch.delenv("HERE_API_KEY", raising=False)
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        r = asyncio.run(routing.get_routing_provider().route(38.5816, -121.4944, 39.5296, -119.8138))
        assert r.source == "mock" and r.truck_aware is False

    def test_geocoders(self):
        self._env()
        from services import geocode
        from services import http
        h = asyncio.run(geocode._here("Reno, NV", 38.58, -121.49))
        http._client = None
        o = asyncio.run(geocode._ors("Reno, NV", 38.58, -121.49))
        print("GEO here:", h[0].address, h[0].lat, "ors:", o[0].address, o[0].lat)
        assert h and abs(h[0].lat - 39.53) < 0.2
        assert o and abs(o[0].lat - 39.53) < 0.2
