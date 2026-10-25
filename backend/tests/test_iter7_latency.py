# Iteration 7: answer latency, templated route replies, background parking warm-up, chat still chat
import os
import re
import time

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
SAC = {"lat": 38.5816, "lng": -121.4944}
FOUND_RE = re.compile(r"^Route set to .+, [\d.,]+ miles away\.( .+\.)?$")
DEST_RE = re.compile(r"^Route to .+ is set: .+, [\d.,]+ miles\.")


@pytest.fixture(scope="module")
def s():
    x = requests.Session()
    x.headers.update({"Content-Type": "application/json"})
    return x


def _agent(s, text, **kw):
    t0 = time.time()
    r = s.post(f"{BASE_URL}/api/agent", json={"text": text, **SAC, **kw}, timeout=120)
    return r, time.time() - t0


# Latency + templated reply for stop search
def test_fuel_fast_templated(s):
    r, dt = _agent(s, "I need fuel")
    d = r.json()
    print(f"FUEL {dt:.1f}s:", d["reply"], "| parking:", d["parking"] and d["parking"]["verdict"])
    assert r.status_code == 200 and d["status"] == "route_set"
    assert FOUND_RE.match(d["reply"]), d["reply"]
    assert dt < 10


# Latency + templated reply for destination
def test_reno_fast_templated(s):
    r, dt = _agent(s, "take me to Reno")
    d = r.json()
    print(f"RENO {dt:.1f}s:", d["reply"])
    assert r.status_code == 200 and d["status"] == "route_set"
    assert DEST_RE.match(d["reply"]), d["reply"]
    assert d["parking"] is None
    assert dt < 10


# 'thanks' stays a chat turn
def test_thanks_chat(s):
    r, dt = _agent(s, "thanks")
    d = r.json()
    print(f"THANKS {dt:.1f}s:", d["status"], d["reply"])
    assert d["status"] == "chat" and d["reply"] and d.get("route") is None


# Hotel search, then parking warmed in background -> /api/parking for chosen place is fast/cached
def test_hotel_then_parking_followup(s):
    r, dt = _agent(s, "I'm tired, find me a motel")
    d = r.json()
    print(f"HOTEL {dt:.1f}s:", d["reply"], "| parking:", d["parking"])
    assert d["status"] == "route_set" and FOUND_RE.match(d["reply"])
    t0 = time.time()
    p = s.post(f"{BASE_URL}/api/parking", json={"place": d["place"]}, timeout=90)
    print(f"PARKING follow-up {time.time() - t0:.1f}s:", p.status_code, p.json() if p.ok else p.text)
    assert p.status_code == 200
    pk = p.json()
    assert pk["verdict"] in ("listed", "likely", "unlikely", "unclear") and pk["image_path"].startswith("/satellite?")


# Voice end-to-end
def test_voice_fast(s):
    if not os.path.exists("/tmp/q.mp3"):
        pytest.skip("/tmp/q.mp3 missing")
    t0 = time.time()
    with open("/tmp/q.mp3", "rb") as f:
        r = requests.post(f"{BASE_URL}/api/voice", files={"audio": ("q.mp3", f, "audio/mpeg")},
                          data={k: str(v) for k, v in SAC.items()}, timeout=120)
    dt = time.time() - t0
    d = r.json()
    print(f"VOICE {dt:.1f}s:", d["transcript"], "|", d["reply"], "| audio:", d.get("audio_url"))
    assert r.status_code == 200 and d["status"] == "route_set" and d["audio_url"]
    assert dt < 10
