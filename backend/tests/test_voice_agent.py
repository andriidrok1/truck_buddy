"""TruckBuddy real voice agent tests: /api/agent (GPT-5.5 tools), /api/voice (Whisper), /api/voice/audio (TTS)."""
import os
import uuid

import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
LAT, LNG = 38.5816, -121.4944
T = 90


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    return sess


@pytest.fixture
def sid():
    """A fresh dialog per test: the backend keeps session state in memory for the life of the process, so a
    fixed id would carry rejected places and history across tests (and runs) until every mock stop is used up."""
    return f"va-{uuid.uuid4().hex[:8]}"


def _agent(s, text, speak=False, sid=None, **extra):
    body = {"text": text, "lat": LAT, "lng": LNG, "session_id": sid or f"va-{uuid.uuid4().hex[:8]}", **extra}
    r = s.post(f"{API}/agent", params={"speak": "true"} if speak else None, json=body, timeout=T)
    assert r.status_code == 200, r.text
    return r.json()


# Agent: place intents -> route_set with expected category
@pytest.mark.parametrize("text,cat", [
    ("I need fuel", "truck_stop"),
    ("find me a hotel with truck parking", "hotel"),
    ("I'm tired", "hotel"),
    ("need to sleep", "hotel"),
    ("I need a quick break to stretch", "rest_area"),
])
def test_agent_route_set(s, sid, text, cat):
    d = _agent(s, text, sid=sid)
    assert d["status"] == "route_set", d
    assert d["place"] and d["route"], d
    assert d["place"]["category"] == cat, f"{text!r} -> {d['place']['category']} reply={d['reply']}"
    assert d["category"] == cat
    assert d["route"]["distance_mi"] > 0
    assert d["reply"] and len(d["reply"]) < 300
    assert d["transcript"] == text


# Agent: chat with active route does NOT change route
def test_agent_how_far_chat(s, sid):
    d = _agent(s, "how far is it?", sid=sid, active_place_id="ts-49er")
    assert d["status"] == "chat", d
    assert d["place"] is None and d["route"] is None
    assert d["reply"]
    print("how far reply:", d["reply"])


def test_agent_thanks_chat(s, sid):
    d = _agent(s, "thanks", sid=sid)
    assert d["status"] == "chat", d
    assert d["place"] is None


# Agent: not understood
@pytest.mark.parametrize("text", ["", "   ", "123 456", "?!"])
def test_agent_not_understood(s, sid, text):
    d = _agent(s, text, sid=sid)
    assert d["status"] == "not_understood"
    assert d["reply"] == "Sorry, I didn't catch that. Try again."


def test_agent_gibberish(s, sid):
    d = _agent(s, "blorf zxqv grumpt", sid=sid)
    print("gibberish:", d["status"], d["reply"])
    assert d["status"] in ("not_understood", "chat")
    assert d["place"] is None


# Agent speak=true -> audio_url + audio fetch is mp3
def test_agent_speak_audio(s, sid):
    d = _agent(s, "find a truck stop", speak=True, sid=sid)
    assert d["status"] == "route_set"
    assert d.get("audio_url", "").startswith("/api/voice/audio/"), d
    r = s.get(f"{BASE_URL}{d['audio_url']}", timeout=T)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert len(r.content) > 1000


def test_voice_audio_unknown_404(s, sid):
    r = s.get(f"{API}/voice/audio/does-not-exist", timeout=T)
    assert r.status_code == 404


# Voice multipart: real speech mp3 -> transcript + route
def test_voice_real_speech(s, sid):
    with open("/tmp/q.mp3", "rb") as f:
        r = s.post(f"{API}/voice", files={"audio": ("q.mp3", f, "audio/mpeg")},
                   data={"lat": str(LAT), "lng": str(LNG), "session_id": sid}, timeout=T)
    assert r.status_code == 200, r.text
    d = r.json()
    print("voice:", d["transcript"], d["status"], d["reply"])
    assert "fuel" in d["transcript"].lower()
    assert d["status"] == "route_set"
    assert d["place"]["category"] == "truck_stop"
    assert d["audio_url"]


def test_voice_with_active_place(s, sid):
    with open("/tmp/q.mp3", "rb") as f:
        r = s.post(f"{API}/voice", files={"audio": ("q.mp3", f, "audio/mpeg")},
                   data={"lat": str(LAT), "lng": str(LNG), "active_place_id": "ts-49er", "session_id": sid}, timeout=T)
    assert r.status_code == 200, r.text


def test_voice_empty_audio_400(s, sid):
    r = s.post(f"{API}/voice", files={"audio": ("e.webm", b"", "audio/webm")},
               data={"lat": str(LAT), "lng": str(LNG), "session_id": sid}, timeout=T)
    assert r.status_code == 400


def test_voice_garbage_audio_not_understood(s, sid):
    r = s.post(f"{API}/voice", files={"audio": ("g.webm", b"\x00\x01garbage" * 100, "audio/webm")},
               data={"lat": str(LAT), "lng": str(LNG), "session_id": sid}, timeout=T)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "not_understood"
    assert d["reply"] == "Sorry, I didn't catch that. Try again."


def test_voice_missing_fields_422(s, sid):
    r = s.post(f"{API}/voice", data={"lat": str(LAT)}, timeout=T)
    assert r.status_code == 422
