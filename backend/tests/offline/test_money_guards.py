"""The paid endpoints around a copilot turn keep to the same daily money cap and turn budget as the turn itself
(services/spend.py, services/quota.py), identical clips are made once, and provider keys never reach the logs.
Audit 2026-11-13 with mocked providers."""
from types import SimpleNamespace

import pytest
from conftest import run

from models import AgentResponse, Place
from services import breakdown, quota, spend, voice

SAC = {"lat": 38.5816, "lng": -121.4944}
AUDIO = {"audio": ("speech.m4a", b"x" * 2000, "audio/mp4")}
PLACE = Place(id="p1", name="Pilot", category="truck_stop", lat=38.86, lng=-121.95)


@pytest.fixture(autouse=True)
def _fresh():
    quota.reset()
    spend.reset()
    getattr(voice, "_SAME_TEXT", {}).clear()
    yield
    quota.reset()
    spend.reset()
    getattr(voice, "_SAME_TEXT", {}).clear()


def _spent(monkeypatch, frac):
    monkeypatch.setenv("DAILY_USD_CAP", "1")
    spend._pending[(quota.day(), "usd:openai")] = frac


class _Stt:
    """Fake OpenAI transcriptions: a list of results or exceptions, one per call; records the models asked."""

    def __init__(self, *results):
        self.results = list(results)
        self.models: list[str] = []

    async def create(self, **kw):
        self.models.append(kw["model"])
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _fake_openai(monkeypatch, stt):
    from services import llm
    monkeypatch.setattr(llm, "_client", SimpleNamespace(audio=SimpleNamespace(transcriptions=stt)))


def _no_stt(monkeypatch) -> list:
    """Speech-to-text that must not run (the endpoints swallow its errors, so the calls are counted)."""
    calls = []

    async def boom(*a, **k):
        calls.append(a)
        raise AssertionError("speech-to-text must not run")
    monkeypatch.setattr(voice, "transcribe_full", boom)
    monkeypatch.setattr(voice, "transcribe", boom)
    return calls


# ---- 1. /voice/transcribe -------------------------------------------------------------------------------------

def test_transcribe_at_no_ai_skips_stt_and_lets_the_turn_say_the_paused_line(monkeypatch, api_client, agent_sandbox):
    _spent(monkeypatch, 1.2)
    stt = _no_stt(monkeypatch)
    r = api_client.post("/api/voice/transcribe", files=AUDIO)
    body = r.json()
    assert r.status_code == 200 and body["transcript"]  # not empty: the app goes on to /agent ...
    turn = api_client.post("/api/agent", json={"text": body["transcript"], **SAC}).json()
    assert turn["reply"] == spend.AI_PAUSED  # ... which says the stored line, no model call
    assert agent_sandbox.llm.calls == []
    # a doubtful recording (no clear speech) stays silence, as before
    assert api_client.post("/api/voice/transcribe", files=AUDIO, data={"maybe": "1"}).json()["transcript"] == ""
    assert stt == []


def test_transcribe_out_of_turns_skips_stt(monkeypatch, api_client, agent_sandbox):
    monkeypatch.setenv("TURNS_PER_DAY", "1")
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure thing."}
    assert api_client.post("/api/agent", json={"text": "hi", **SAC}).json()["reply"] == "Sure thing."
    stt = _no_stt(monkeypatch)
    body = api_client.post("/api/voice/transcribe", files=AUDIO).json()
    assert body["transcript"] and stt == []
    assert api_client.post("/api/agent", json={"text": body["transcript"], **SAC}).json()["reply"] == quota.OUT_OF_TURNS


def test_transcribe_does_not_spend_a_turn(monkeypatch, api_client, agent_sandbox):
    """The voice turn is /voice/transcribe + /agent: one turn, taken by /agent."""
    monkeypatch.setenv("TURNS_PER_DAY", "1")
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure thing."}

    async def stt(*a, **k):
        return voice.Transcript("find me a truck stop", 0.9)

    monkeypatch.setattr(voice, "transcribe_full", stt)
    assert api_client.post("/api/voice/transcribe", files=AUDIO).json()["transcript"] == "find me a truck stop"
    assert api_client.post("/api/agent", json={"text": "find me a truck stop", **SAC}).json()["reply"] == "Sure thing."


def test_no_whisper_retry_after_a_billed_call(monkeypatch):
    """The prompt-echo retry failed after the first call was already paid for: no third, Whisper, call."""
    echo = SimpleNamespace(text=voice.PROMPT, usage={"type": "duration", "seconds": 3})
    stt = _Stt(echo, RuntimeError("upstream 500"), SimpleNamespace(text="should not run", segments=[]))
    _fake_openai(monkeypatch, stt)
    tr = run(voice.transcribe_full(b"x" * 1000, "a.m4a"))
    assert tr.text == "" and stt.models == [voice.STT_MODEL, voice.STT_MODEL]


def test_failed_call_still_falls_back_to_whisper(monkeypatch):
    stt = _Stt(RuntimeError("upstream 500"), SimpleNamespace(text="take me to Reno", segments=[], duration=2))
    _fake_openai(monkeypatch, stt)
    assert run(voice.transcribe_full(b"x" * 1000, "a.m4a")).text == "take me to Reno"
    assert stt.models == [voice.STT_MODEL, "whisper-1"]


def test_no_whisper_fallback_once_ai_is_paused(monkeypatch):
    _spent(monkeypatch, 1.2)
    stt = _Stt(RuntimeError("upstream 500"), SimpleNamespace(text="should not run", segments=[]))
    _fake_openai(monkeypatch, stt)
    assert run(voice.transcribe_full(b"x" * 1000, "a.m4a")).text == ""
    assert stt.models == [voice.STT_MODEL]


# ---- 1. /onboarding/parse -------------------------------------------------------------------------------------

def test_onboarding_parse_at_no_ai_makes_no_paid_call(monkeypatch, api_client, fake_llm):
    _spent(monkeypatch, 1.2)
    stt = _no_stt(monkeypatch)
    r = api_client.post("/api/onboarding/parse", files=AUDIO, data={"step": "name"})
    assert r.status_code == 200 and r.json()["transcript"] == "" and r.json()["updates"] == {}
    assert fake_llm.calls == [] and stt == []


def test_onboarding_parse_counts_as_a_turn(monkeypatch, api_client, fake_llm):
    monkeypatch.setenv("TURNS_PER_DAY", "1")
    fake_llm.default = '{"driver_name": "Mike"}'

    async def stt(*a, **k):
        return "my name is Mike"

    monkeypatch.setattr(voice, "transcribe", stt)
    first = api_client.post("/api/onboarding/parse", files=AUDIO, data={"step": "name"}).json()
    assert first["updates"] == {"driver_name": "Mike"}
    stt = _no_stt(monkeypatch)
    again = api_client.post("/api/onboarding/parse", files=AUDIO, data={"step": "name"}).json()
    assert again["transcript"] == "" and again["updates"] == {} and len(fake_llm.calls) == 1 and stt == []


# ---- 1. /parking/answer ---------------------------------------------------------------------------------------

def _count_synth(monkeypatch):
    made = []

    async def synth(text, *a, **k):
        made.append(text)
        return f"clip{len(made):030d}"

    monkeypatch.setattr(voice, "synthesize", synth)
    monkeypatch.setattr(voice, "get_audio", lambda aid: b"mp3")
    monkeypatch.setattr(voice, "is_fallback", lambda aid: False)
    return made


def test_parking_answer_makes_one_clip_per_text(monkeypatch, api_client):
    from services import approach

    async def answer(place, wait):
        return "ready", "Pilot has truck parking, enter from the east side.", None

    monkeypatch.setattr(approach, "answer", answer)
    made = _count_synth(monkeypatch)
    urls = [api_client.post("/api/parking/answer", json={"place": PLACE.model_dump(), "wait_s": 1}).json()["audio_url"]
            for _ in range(3)]
    assert len(made) == 1 and len(set(urls)) == 1 and urls[0]


def test_parking_answer_at_no_ai_makes_no_new_clip(monkeypatch, api_client):
    from services import approach

    async def answer(place, wait):
        return "ready", "Pilot has truck parking.", None

    monkeypatch.setattr(approach, "answer", answer)
    made = _count_synth(monkeypatch)
    _spent(monkeypatch, 1.2)
    body = api_client.post("/api/parking/answer", json={"place": PLACE.model_dump(), "wait_s": 1}).json()
    assert body["reply"] == "Pilot has truck parking." and body["audio_url"] is None and made == []


# ---- 1. /breakdown/{rid}?speak=true ---------------------------------------------------------------------------

def test_breakdown_result_is_spoken_once(monkeypatch, api_client):
    res = AgentResponse(status="providers", transcript="", reply="Three shops nearby. Joe's Diesel is closest.",
                        source="model")

    async def result(rid):
        return res.model_copy(deep=True) if rid == "r1" else None

    monkeypatch.setattr(breakdown, "result", result)
    made = _count_synth(monkeypatch)
    later = []
    monkeypatch.setattr(voice, "synthesize_later", lambda text, **k: later.append(text) or "later-id")
    bodies = [api_client.get("/api/breakdown/r1?speak=true").json() for _ in range(3)]
    assert len(made) == 1 and len(later) <= 1
    assert len({b["audio_url"] for b in bodies}) == 1 and bodies[0]["audio_url"]
    assert api_client.get("/api/breakdown/nope?speak=true").status_code == 404
