# Iteration 11: conversational copilot - rotating templates, rejected places, driver name, TTS parts, /api/speak voices
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
LAT, LNG = 38.5816, -121.4944
T = 120
PROFILE = {"driver_name": "Mike"}
TURNS = ["I need fuel", "not that one", "something else", "I'm tired, find me a motel", "take me to Reno"]


def agent(text, sid, active=None):
    body = {"text": text, "lat": LAT, "lng": LNG, "session_id": sid, "truck_profile": PROFILE}
    if active:
        body["active_place"] = active
    r = requests.post(f"{BASE_URL}/api/agent", params={"speak": "true"}, json=body, timeout=T)
    assert r.status_code == 200, r.text
    return r.json()


def _dialog(with_active):
    sid = f"TEST_conv_{uuid.uuid4().hex[:8]}"
    out, active = [], None
    for t in TURNS:
        res = agent(t, sid, active if with_active else None)
        print(f"[{t}] -> {res['status']} | {res.get('reply')} | place={res.get('place', {}) and res['place']['name']}")
        out.append(res)
        if res.get("place"):
            active = res["place"]
    return out


@pytest.fixture(scope="module")
def dialog():
    """Driver never sends active_place (hardest case for 'not that one')."""
    return _dialog(with_active=False)


@pytest.fixture(scope="module")
def dialog_active():
    """App-like: active_place = last route."""
    return _dialog(with_active=True)


# Dialog: route_set every turn
@pytest.mark.parametrize("fx", ["dialog", "dialog_active"])
def test_every_turn_route_set(fx, request):
    turns = request.getfixturevalue(fx)
    assert [t["status"] for t in turns] == ["route_set"] * 5, [t["reply"] for t in turns]


# Dialog: 'not that one' / 'something else' give new, never-rejected places
@pytest.mark.parametrize("fx", ["dialog", "dialog_active"])
def test_rejected_places_not_repeated(fx, request):
    t = request.getfixturevalue(fx)
    ids = [t[i]["place"]["id"] for i in range(3)]
    assert len(set(ids)) == 3, [t[i]["place"]["name"] for i in range(3)]
    assert t[3]["category"] == "hotel"
    assert t[4]["place"]["category"] == "destination"


# Dialog: templates rotate; driver name about every third answer
@pytest.mark.parametrize("fx", ["dialog", "dialog_active"])
def test_templates_rotate_and_name(fx, request):
    t = request.getfixturevalue(fx)
    intros = [re.split(r"(?<=[.!?])\s+", x["reply"], maxsplit=1)[0] for x in t[:3]]
    assert len(set(intros)) == 3, intros
    named = [i for i, x in enumerate(t) if "Mike" in x["reply"]]
    assert named == [2], named


# Dialog: both audio parts are served as audio/mpeg, timings.tts present
def test_audio_parts_and_timings(dialog):
    for x in dialog:
        assert "tts" in (x.get("timings") or {}), x.get("timings")
        assert x.get("audio_url") and x.get("audio_url_2"), x
        for key in ("audio_url", "audio_url_2"):
            r = requests.get(f"{BASE_URL}{x[key]}", timeout=30)
            assert r.status_code == 200 and r.headers["content-type"].startswith("audio/mpeg"), (key, r.status_code)
            assert len(r.content) > 1000


# /api/speak: per-voice cache and validation
VOICES = {"brian": "nPczCjzI2devNBz1zQrb", "chris": "iP95p4xoKVk53GoZ742B", "laura": "FGY2WhTYpPnrIDTdsKH5"}


def speak(text, vid=None):
    body = {"text": text}
    if vid:
        body["eleven_voice_id"] = vid
    return requests.post(f"{BASE_URL}/api/speak", json=body, timeout=60)


@pytest.mark.usefixtures("elevenlabs_tts")  # eleven_voice_id only changes the audio with ElevenLabs
def test_speak_voices_distinct_and_cached():
    text = f"TEST voice compare {uuid.uuid4().hex[:6]}. Pilot is about four miles out."
    urls = {}
    for name, vid in [("default", None), *VOICES.items()]:
        r = speak(text, vid)
        assert r.status_code == 200, (name, r.text)
        urls[name] = r.json()["audio_url"]
        a = requests.get(f"{BASE_URL}{urls[name]}", timeout=30)
        assert a.status_code == 200 and a.headers["content-type"].startswith("audio/mpeg")
    assert len(set(urls.values())) == 4, urls
    for name, vid in VOICES.items():
        assert speak(text, vid).json()["audio_url"] == urls[name]


@pytest.mark.parametrize("bad", ["bad id!", "short", "x" * 41, "abc-def-ghi-jkl"])
def test_speak_invalid_voice_id(bad):
    assert speak("hello", bad).status_code == 422


# /api/speak: 8 parallel new texts all succeed (semaphore + 429 retry)
def test_speak_parallel_8():
    tag = uuid.uuid4().hex[:6]
    texts = [f"TEST parallel line {i} {tag}, the stop is right off Interstate 80." for i in range(8)]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda t: speak(t), texts))
    assert all(r.status_code == 200 for r in res), [r.status_code for r in res]
    assert len({r.json()["audio_url"] for r in res}) == 8


# Health: ElevenLabs is the TTS provider (skipped when the backend runs OpenAI TTS)
@pytest.mark.usefixtures("elevenlabs_tts")
def test_health_tts():
    r = requests.get(f"{BASE_URL}/api/health", timeout=15)
    assert r.status_code == 200 and r.json()["providers"]["tts"] == "elevenlabs"
