# Onboarding feature: /api/speak TTS cache, /api/onboarding/parse voice extraction, TruckProfile prefs, avoid_tolls routing
import os

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
SAC = {"lat": 38.5816, "lng": -121.4944}
TP = {"height_in": 162, "trailer_ft": 53, "weight_lb": 80000, "hazmat": False}


@pytest.fixture(scope="module")
def s():
    return requests.Session()


def _parse(s, path, step):
    with open(path, "rb") as f:
        return s.post(f"{BASE_URL}/api/onboarding/parse", files={"audio": ("a.mp3", f, "audio/mpeg")},
                      data={"step": step}, timeout=120)


# --- /api/speak ---
class TestSpeak:
    def test_speak_and_cache(self, s):
        r1 = s.post(f"{BASE_URL}/api/speak", json={"text": "TEST_ Hello driver, onboarding check."}, timeout=90)
        assert r1.status_code == 200, r1.text
        url = r1.json()["audio_url"]
        assert url.startswith("/api/voice/audio/")
        r2 = s.post(f"{BASE_URL}/api/speak", json={"text": "TEST_ Hello driver, onboarding check."}, timeout=90)
        assert r2.status_code == 200 and r2.json()["audio_url"] == url  # cached per text
        a = s.get(f"{BASE_URL}{url}", timeout=30)
        assert a.status_code == 200
        assert a.headers["content-type"].startswith("audio/mpeg")
        assert len(a.content) > 1000

    @pytest.mark.parametrize("text", ["", "x" * 601])
    def test_speak_invalid(self, s, text):
        r = s.post(f"{BASE_URL}/api/speak", json={"text": text}, timeout=30)
        assert r.status_code == 422

    def test_speak_missing_body(self, s):
        assert s.post(f"{BASE_URL}/api/speak", json={}, timeout=30).status_code == 422


# --- /api/onboarding/parse ---
class TestOnboardingParse:
    def test_name(self, s):
        r = _parse(s, "/tmp/onb_name.mp3", "name")
        assert r.status_code == 200, r.text
        d = r.json()
        print(d)
        assert "mike" in d["transcript"].lower()
        assert d["updates"].get("driver_name") == "Mike"

    def test_truck(self, s):
        r = _parse(s, "/tmp/onb_truck.mp3", "truck")
        assert r.status_code == 200, r.text
        d = r.json()
        print(d)
        u = d["updates"]
        assert u.get("height_in") == 162
        assert u.get("trailer_ft") == 53
        assert u.get("weight_lb") == 80000
        assert u.get("hazmat") is False

    def test_prefs(self, s):
        r = _parse(s, "/tmp/onb_prefs.mp3", "prefs")
        assert r.status_code == 200, r.text
        d = r.json()
        print(d)
        u = d["updates"]
        assert u.get("avoid_tolls") is True
        assert "Love's" in u.get("favorite_chains", []) and "Pilot" in u.get("favorite_chains", [])
        assert u.get("break_hours") == 4

    def test_invalid_step(self, s):
        r = _parse(s, "/tmp/onb_name.mp3", "bogus")
        assert r.status_code == 422

    def test_empty_audio(self, s):
        r = s.post(f"{BASE_URL}/api/onboarding/parse", files={"audio": ("a.mp3", b"", "audio/mpeg")},
                   data={"step": "name"}, timeout=30)
        assert r.status_code == 400

    def test_garbage_audio_returns_empty_updates(self, s):
        r = s.post(f"{BASE_URL}/api/onboarding/parse", files={"audio": ("a.mp3", b"notaudio" * 50, "audio/mpeg")},
                   data={"step": "name"}, timeout=60)
        assert r.status_code == 200
        assert r.json()["updates"] == {}


# --- TruckProfile prefs + routing ---
class TestProfilePrefs:
    @pytest.mark.parametrize("bh", [0, 20])
    def test_invalid_break_hours(self, s, bh):
        r = s.post(f"{BASE_URL}/api/agent", json={"text": "hi", **SAC, "truck_profile": {**TP, "break_hours": bh}}, timeout=60)
        assert r.status_code == 422

    def test_too_many_chains(self, s):
        r = s.post(f"{BASE_URL}/api/agent", json={"text": "hi", **SAC, "truck_profile": {**TP, "favorite_chains": ["a"] * 9}}, timeout=60)
        assert r.status_code == 422

    def test_avoid_tolls_changes_route(self, s):
        res = {}
        for avoid in (False, True):
            tp = {**TP, "avoid_tolls": avoid, "driver_name": "Mike"}
            r = s.post(f"{BASE_URL}/api/agent", json={"text": "take me to San Francisco", **SAC, "truck_profile": tp}, timeout=120)
            assert r.status_code == 200, r.text
            d = r.json()
            print(avoid, d.get("status"), d.get("reply"), d.get("route", {}) and {k: v for k, v in d["route"].items() if k != "geometry"})
            assert d.get("route"), d
            res[avoid] = d
        a, b = res[False]["route"], res[True]["route"]
        assert a.get("source") == b.get("source")
        assert (a.get("distance_m"), a.get("duration_s")) != (b.get("distance_m"), b.get("duration_s")) or a.get("geometry") != b.get("geometry")
