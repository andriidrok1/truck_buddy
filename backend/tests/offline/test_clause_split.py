"""The first TTS clip ends at the first clause, so the driver hears the reply sooner."""
from types import SimpleNamespace

import pytest
from conftest import fixture_json

from models import Place
from services import voice
from services.speech_text import split_reply

SAC = {"lat": 38.5816, "lng": -121.4944}


@pytest.mark.parametrize("text,first,rest", [
    # A short first sentence already makes a short first clip; the clause rule only cuts inside it.
    ("Alright, got one. Pilot Travel Center, right off Main St, about 6.2 miles out.",
     "Alright, got one.", "Pilot Travel Center, right off Main St, about 6.2 miles out."),
    ("Okay, Pilot is about 6 miles ahead.", "Okay, Pilot is about 6 miles ahead.", ""),  # "Okay," is too short
    ("Here's another way via I-80 and CA-89, 4 h 38 min, 204 miles.",
     "Here's another way via I-80 and CA-89,", "4 h 38 min, 204 miles."),
    ("Sure thing. The Pilot in Lodi has showers.", "Sure thing.", "The Pilot in Lodi has showers."),
    ("Head to St. Louis on U.S. 50 — then take the exit.", "Head to St. Louis on U.S. 50", "then take the exit."),
    ("One two three four, five.", "One two three four, five.", ""),  # rest would be one word
    ("", "", ""),
    ("   spaced   out   text.  ", "spaced out text.", ""),
])
def test_split_reply(text, first, rest):
    assert split_reply(text) == (first, rest)


def test_sentence_mode_when_clause_split_is_off():
    t = "Alright, got one. Pilot Travel Center, right off Main St."
    assert split_reply(t, clause=False) == ("Alright, got one.", "Pilot Travel Center, right off Main St.")
    assert split_reply("Only one sentence here, with a comma.", clause=False) == \
        ("Only one sentence here, with a comma.", "")


@pytest.fixture
def tts(monkeypatch):
    calls = []

    class _Speech:
        async def create(self, **kw):
            calls.append(kw["input"])
            return SimpleNamespace(content=b"ID3x")

    monkeypatch.setattr(voice.llm, "client", lambda: SimpleNamespace(audio=SimpleNamespace(speech=_Speech())))

    async def no_boost(mp3):
        return mp3

    monkeypatch.setattr(voice, "louder", no_boost)
    monkeypatch.delenv("ELEVENLABS_ENABLED", raising=False)
    return calls


def test_agent_reply_is_split_at_the_clause(api_client, agent_sandbox, fake_http, tts):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.add("cwwp2.dot.ca.gov", "/data/", json={"data": []})
    pilot = Place(id="g1", name="Pilot Travel Center", category="truck_stop", lat=38.58, lng=-121.55,
                  address="3645 W Capitol Ave, West Sacramento, CA", distance_mi=3.4)
    agent_sandbox.places_provider.places = [pilot]
    agent_sandbox.llm.on(r"truck stop", {"intent": "find", "category": "truck_stop"})
    r = api_client.post("/api/agent?speak=true", json={"text": "find a truck stop", **SAC})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"].startswith("Alright, got one. Pilot Travel Center, right off W Capitol Ave,")
    assert body["reply_parts"][0] == "Alright, got one."
    assert body["reply_parts"][1].startswith("Pilot Travel Center, right off W Capitol Ave,")
    assert len(body["reply_parts"]) == 2 and body["audio_url"] and body["audio_url_2"]
    assert "Alright, got one." in tts  # the first clip is the short first sentence


def test_flag_off_splits_at_the_sentence(api_client, agent_sandbox, fake_http, tts, monkeypatch):
    monkeypatch.setenv("TTS_CLAUSE_SPLIT", "0")
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure thing, no problem. The Pilot has showers."}
    r = api_client.post("/api/agent?speak=true", json={"text": "do they have showers", **SAC})
    assert r.json()["reply_parts"] == ["Sure thing, no problem.", "The Pilot has showers."]
    assert "Sure thing, no problem." in tts


def test_single_short_reply_has_one_part(api_client, agent_sandbox, tts):
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure."}
    r = api_client.post("/api/agent?speak=true", json={"text": "ok", **SAC})
    body = r.json()
    assert body["reply_parts"] == ["Sure."] and body["audio_url"] and body["audio_url_2"] is None
    assert voice.get_audio(body["audio_url"].rsplit("/", 1)[-1]) == b"ID3x"
