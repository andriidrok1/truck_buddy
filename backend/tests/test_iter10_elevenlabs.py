"""Iteration 10: ElevenLabs primary TTS + OpenAI fallback, agent dialog chain with speak=true, transcribe, speak cache, warm."""
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
LAT, LNG = 38.5816, -121.4944
T = 90
TIMINGS = {}


def agent(text, sid, **extra):
    body = {"text": text, "lat": LAT, "lng": LNG, "session_id": sid, **extra}
    r = requests.post(f"{BASE_URL}/api/agent", params={"speak": "true"}, json=body, timeout=T)
    assert r.status_code == 200, r.text
    return r.json()


def fetch_audio(path):
    r = requests.get(f"{BASE_URL}{path}", timeout=T)
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert len(r.content) > 1000
    # mp3: ID3 tag or MPEG frame sync
    assert r.content[:3] == b"ID3" or (r.content[0] == 0xFF and (r.content[1] & 0xE0) == 0xE0)
    return r.content


# Health: TTS provider
def test_health_tts_elevenlabs():
    r = requests.get(f"{BASE_URL}/api/health", timeout=30)
    assert r.status_code == 200
    assert r.json()["providers"]["tts"] == "elevenlabs"


# Dialog chain find -> question -> not that one -> bye with speak=true
@pytest.fixture(scope="module")
def chain():
    sid = f"TEST_{uuid.uuid4().hex[:12]}"
    out = {}
    t1 = agent("find me a truck stop", sid)
    out["t1"] = t1
    rt = t1.get("route") or {}
    t2 = agent("how far is it?", sid, active_place=t1.get("place"),
               active_distance_mi=rt.get("distance_mi"), active_duration_min=rt.get("duration_min"))
    out["t2"] = t2
    t3 = agent("not that one", sid, active_place=t1.get("place"))
    out["t3"] = t3
    t4 = agent("bye", sid, active_place=t3.get("place"))
    out["t4"] = t4
    return out


def test_chain_turn1_route_set(chain):
    t1 = chain["t1"]
    assert t1["status"] == "route_set", t1
    assert t1["status"] == "route_set" and " miles" in t1["reply"], t1["reply"]
    assert t1["place"] and t1["route"]
    assert t1["audio_url"]
    fetch_audio(t1["audio_url"])
    assert "llm_round_1" in t1["timings"] and "tts" in t1["timings"]
    TIMINGS["t1"] = t1["timings"]


def test_chain_turn2_chat_uses_numbers(chain):
    t1, t2 = chain["t1"], chain["t2"]
    assert t2["status"] == "chat", t2
    assert t2["end_conversation"] is False
    d = t1["route"]["distance_mi"]
    reply = t2["reply"]
    # reply should mention the distance (rounded) or duration
    nums = {str(round(d)), f"{d:.1f}", str(int(d)), str(t1["route"]["duration_min"])}
    assert any(n in reply for n in nums), (reply, nums)
    assert t2["audio_url"]
    fetch_audio(t2["audio_url"])
    if t2.get("audio_url_2"):
        fetch_audio(t2["audio_url_2"])
    TIMINGS["t2"] = t2["timings"]


def test_chain_turn3_different_place(chain):
    t1, t3 = chain["t1"], chain["t3"]
    assert t3["status"] == "route_set", t3
    assert t3["place"]["id"] != t1["place"]["id"]
    assert t3["audio_url"]
    fetch_audio(t3["audio_url"])
    TIMINGS["t3"] = t3["timings"]


def test_chain_turn4_bye(chain):
    t4 = chain["t4"]
    assert t4["status"] == "chat", t4
    assert t4["end_conversation"] is True
    assert t4["audio_url"]
    fetch_audio(t4["audio_url"])
    assert "tts" in t4["timings"]
    TIMINGS["t4"] = t4["timings"]


def test_chain_tts_latency(chain):
    tts = [chain[k]["timings"]["tts"] for k in ("t1", "t2", "t3", "t4")]
    print("TTS ms per turn:", tts, "timings:", {k: chain[k]["timings"] for k in chain})
    assert sorted(tts)[len(tts) // 2] < 1000, tts


# Two-sentence reply: audio_url and audio_url_2 both playable
def test_two_sentence_reply_audio_url_2():
    sid = f"TEST_{uuid.uuid4().hex[:12]}"
    t1 = agent("find me a truck stop", sid)
    rt = t1["route"]
    t2 = agent("how far is it and where do I park?", sid, active_place=t1["place"],
               active_distance_mi=rt["distance_mi"], active_duration_min=rt["duration_min"])
    assert t2["status"] == "chat"
    print("2-sentence reply:", t2["reply"], t2.get("audio_url_2"))
    fetch_audio(t2["audio_url"])
    assert t2.get("audio_url_2"), f"expected 2 sentences: {t2['reply']}"
    fetch_audio(t2["audio_url_2"])
    assert t2["audio_url"] != t2["audio_url_2"]


# /api/speak cache and OpenAI comparison fields
def test_speak_cached_same_id():
    text = f"TEST line {uuid.uuid4().hex[:6]}."
    t0 = time.time()
    a = requests.post(f"{BASE_URL}/api/speak", json={"text": text}, timeout=T)
    first = time.time() - t0
    assert a.status_code == 200
    t0 = time.time()
    b = requests.post(f"{BASE_URL}/api/speak", json={"text": text}, timeout=T)
    second = time.time() - t0
    assert b.json()["audio_url"] == a.json()["audio_url"]
    fetch_audio(a.json()["audio_url"])
    print(f"speak first={first:.2f}s second={second:.2f}s")


def test_speak_openai_voice_model_distinct():
    text = f"TEST compare {uuid.uuid4().hex[:6]}."
    e = requests.post(f"{BASE_URL}/api/speak", json={"text": text}, timeout=T).json()["audio_url"]
    o = requests.post(f"{BASE_URL}/api/speak", json={"text": text, "voice": "nova", "model": "tts-1"}, timeout=T)
    assert o.status_code == 200
    o = o.json()["audio_url"]
    assert o != e
    fetch_audio(o)
    m = requests.post(f"{BASE_URL}/api/speak", json={"text": text, "model": "gpt-4o-mini-tts"}, timeout=T)
    assert m.status_code == 200
    fetch_audio(m.json()["audio_url"])


def test_speak_validation():
    assert requests.post(f"{BASE_URL}/api/speak", json={"text": ""}, timeout=30).status_code == 422
    assert requests.post(f"{BASE_URL}/api/speak", json={"text": "hi", "voice": "bogus"}, timeout=30).status_code == 422


def test_audio_unknown_404():
    assert requests.get(f"{BASE_URL}/api/voice/audio/nope{uuid.uuid4().hex}", timeout=30).status_code == 404


# Transcribe an ElevenLabs mp3 then route to Reno
@pytest.fixture(scope="module")
def reno_mp3():
    r = requests.post(f"{BASE_URL}/api/speak", json={"text": "Take me to Reno."}, timeout=T)
    assert r.status_code == 200
    return fetch_audio(r.json()["audio_url"])


def test_transcribe_then_agent_reno(reno_mp3):
    r = requests.post(f"{BASE_URL}/api/voice/transcribe", files={"audio": ("q.mp3", reno_mp3, "audio/mpeg")}, timeout=T)
    assert r.status_code == 200, r.text
    tr = r.json()["transcript"]
    assert "reno" in tr.lower(), tr
    assert "stt" in r.json()["timings"]
    res = agent(tr, f"TEST_{uuid.uuid4().hex[:12]}")
    print("Reno reply:", res["reply"])
    assert res["status"] == "route_set"
    assert res["status"] == "route_set" and "Reno" in res["reply"], res["reply"]
    fetch_audio(res["audio_url"])


def test_transcribe_empty_400():
    r = requests.post(f"{BASE_URL}/api/voice/transcribe", files={"audio": ("q.mp3", b"", "audio/mpeg")}, timeout=30)
    assert r.status_code == 400


# Legacy one-shot /api/voice
def test_legacy_voice(reno_mp3):
    r = requests.post(f"{BASE_URL}/api/voice", files={"audio": ("q.mp3", reno_mp3, "audio/mpeg")},
                      data={"lat": LAT, "lng": LNG, "session_id": f"TEST_{uuid.uuid4().hex[:8]}"}, timeout=T)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "reno" in d["transcript"].lower()
    assert d["status"] == "route_set"
    for k in ("stt", "llm_round_1", "tts", "total"):
        assert k in d["timings"], d["timings"]
    fetch_audio(d["audio_url"])


# Warm returns quickly
def test_warm_fast():
    t0 = time.time()
    r = requests.post(f"{BASE_URL}/api/warm", params={"lat": LAT, "lng": LNG}, timeout=30)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert time.time() - t0 < 3


def test_warm_validation():
    assert requests.post(f"{BASE_URL}/api/warm", params={"lat": 999, "lng": LNG}, timeout=30).status_code == 422
