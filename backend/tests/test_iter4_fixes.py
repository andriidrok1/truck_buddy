# Iteration 4 retests: height 203 in, gibberish rejection, Discover landmarks, friendly notices, destination parking=None
import os
import sys
import asyncio
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
SAC = {"lat": 38.5816, "lng": -121.4944}
DEFAULT_TP = {"height_in": 162, "trailer_ft": 53, "weight_lb": 80000, "hazmat": False}
FRIENDLY = {"Part of the route may be restricted for your truck. Watch posted signs.",
            "Route includes tolls.", "Route includes a ferry.", "Check the route for restrictions."}


@pytest.fixture(scope="module")
def s():
    x = requests.Session()
    x.headers.update({"Content-Type": "application/json"})
    return x


def agent(s, text, tp=DEFAULT_TP):
    return s.post(f"{BASE_URL}/api/agent", json={"text": text, **SAC, "truck_profile": tp}, timeout=120)


# (1) height bound 16'11" = 203 in
class TestHeightBound:
    def test_agent_203_ok(self, s):
        r = agent(s, "take me to Reno, Nevada", {**DEFAULT_TP, "height_in": 203})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "route_set"
        assert d["route"]["truck_aware"] is True

    def test_agent_204_422(self, s):
        r = agent(s, "take me to Reno", {**DEFAULT_TP, "height_in": 204})
        assert r.status_code == 422

    def test_voice_form_203_ok(self):
        path = "/tmp/reno.mp3"
        if not os.path.exists(path):
            pytest.skip("no fixture audio")
        import json
        with open(path, "rb") as f:
            r = requests.post(f"{BASE_URL}/api/voice", files={"audio": ("q.mp3", f, "audio/mpeg")},
                              data={**{k: str(v) for k, v in SAC.items()},
                                    "truck_profile": json.dumps({**DEFAULT_TP, "height_in": 203})}, timeout=180)
        assert r.status_code == 200, r.text


# (2) gibberish destination must not route
class TestGibberish:
    @pytest.mark.parametrize("text", ["take me to Qwxzzyplk Boulevard 99999", "take me to Zzqxv Flurbington"])
    def test_no_route(self, s, text):
        r = agent(s, text)
        assert r.status_code == 200
        d = r.json()
        print(text, "->", d["status"], d.get("reply"), (d.get("place") or {}).get("name"))
        assert d["status"] != "route_set"
        assert d.get("route") is None


# (5) landmarks via HERE Discover
class TestLandmarks:
    @pytest.mark.parametrize("text,expect", [("take me to Port of Oakland", "oakland"),
                                             ("take me to Pilot travel center Lodi", "pilot")])
    def test_landmark(self, s, text, expect):
        r = agent(s, text)
        assert r.status_code == 200
        d = r.json()
        print(text, "->", d["status"], d["place"] and (d["place"]["name"], d["place"].get("address")))
        assert d["status"] == "route_set"
        assert expect in (d["place"]["name"] + " " + (d["place"].get("address") or "")).lower()
        assert d["place"]["id"].startswith("here-")


# (6) notices friendly + deduplicated
class TestNotices:
    def test_tall_truck_notices(self, s):
        r = agent(s, "take me to Reno, Nevada", {**DEFAULT_TP, "height_in": 170})
        d = r.json()
        n = d["route"]["notices"]
        print("notices:", n)
        assert len(n) == len(set(n))
        assert all(x in FRIENDLY for x in n)

    def test_friendly_unit(self):
        sys.path.insert(0, "/app/backend")
        from services.routing import _friendly
        out = _friendly(["violatedVehicleRestriction", "violatedVehicleRestriction", "tollsDataUnavailable", ""])
        assert out == ["Part of the route may be restricted for your truck. Watch posted signs.", "Tolls couldn't be fully avoided on this route."]


# (7) /api/route destination -> parking None
class TestDestinationParking:
    def test_destination_parking_null(self, s):
        place = {"id": "dest-reno", "name": "Reno", "category": "destination", "lat": 39.5296, "lng": -119.8138}
        r = s.post(f"{BASE_URL}/api/route", json={**SAC, "place": place, "truck_profile": DEFAULT_TP}, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["parking"] is None
        assert d["route"]["source"] == "here"

    def test_fuel_place_has_parking(self, s):
        r = agent(s, "I need fuel")
        d = r.json()
        assert d["status"] == "route_set"
        # iter7: parking null unless cached; app calls /api/parking afterwards
        assert d["parking"] is None or d["parking"]["verdict"]
        assert len(d.get("options") or []) >= 1
