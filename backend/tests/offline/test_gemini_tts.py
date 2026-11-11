"""Gemini TTS as the copilot voice (GEMINI_TTS_ENABLED=1): provider order, the fall back to OpenAI with a pause on a
429 / timeout / bad reply, the cache tag, the style by language, its own spend line (gemini_tts) apart from the
Maps grounding call cap, the cheap_voice stage, and the first clip in server._speak."""
import base64
import io
import json
import wave
from types import SimpleNamespace

import httpx
import pytest
from conftest import run

from models import AgentResponse
from services import http, quota, spend, voice

HOST = "generativelanguage.googleapis.com"


def _wav(seconds: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(voice.GEMINI_PCM_RATE)
        w.writeframes(b"\x00\x10" * int(voice.GEMINI_PCM_RATE * seconds))
    return buf.getvalue()


def _reply(audio: bytes, usage: dict | None = None) -> dict:
    body = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(audio).decode()}}]}}]}
    if usage is not None:
        body["usageMetadata"] = usage
    return body


@pytest.fixture
def tts(monkeypatch, fake_http):
    """OpenAI speech canned, ffmpeg skipped, the shared client with the production spend hook, Gemini on."""
    openai_calls = []

    class _Speech:
        async def create(self, **kw):
            openai_calls.append(kw)
            return SimpleNamespace(content=b"ID3openai")

    monkeypatch.setattr(voice.llm, "client", lambda: SimpleNamespace(audio=SimpleNamespace(speech=_Speech())))

    async def no_boost(mp3):
        return mp3

    async def fake_mp3(audio, gain_db=voice.GEMINI_GAIN_DB):
        return b"ID3gemini"

    monkeypatch.setattr(voice, "louder", no_boost)
    monkeypatch.setattr(voice, "pcm_to_mp3", fake_mp3)
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(fake_http), event_hooks=http.HOOKS)
    monkeypatch.delenv("ELEVENLABS_ENABLED", raising=False)
    monkeypatch.setenv("GEMINI_TTS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.delenv("GEMINI_TTS_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_TTS_VOICE", raising=False)
    monkeypatch.setattr(voice, "_gemini_down_until", 0.0)
    return openai_calls


def _gemini_ok(fake_http, usage=None):
    return fake_http.add(HOST, "/v1beta/models/", json=_reply(_wav(), usage))


# ---- switch and order ------------------------------------------------------------------------------------------

def test_off_by_default(monkeypatch):
    monkeypatch.delenv("GEMINI_TTS_ENABLED", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    assert voice.gemini_voice() is None and voice.provider() == "openai" and not voice.gemini_active()
    monkeypatch.setenv("GEMINI_TTS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert voice.gemini_voice() is None  # no key, no Gemini


def test_gemini_speaks_first_with_the_text_verbatim_and_the_style_beside_it(tts, fake_http):
    _gemini_ok(fake_http)
    aid = run(voice.synthesize("Alright, got one."))
    assert voice.get_audio(aid) == b"ID3gemini" and not voice.is_fallback(aid) and tts == []
    req = fake_http.calls_to(HOST)[0]
    assert req.url.path == "/v1beta/models/gemini-3.8-flash-lite-tts:generateContent"
    assert req.headers["x-goog-api-key"] == "test-gemini"
    body = json.loads(req.content)
    part = body["contents"][0]["parts"][0]
    assert part["text"] == "Alright, got one."  # only the line: the model reads the text part aloud word for word
    assert part["speech_metadata"]["style"] == voice.GEMINI_STYLE
    assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Achird"
    assert voice.provider() == "gemini"


def test_model_and_voice_from_env(tts, fake_http, monkeypatch):
    monkeypatch.setenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
    monkeypatch.setenv("GEMINI_TTS_VOICE", "Sulafat")
    _gemini_ok(fake_http)
    run(voice.synthesize("Hi."))
    req = fake_http.calls_to(HOST)[0]
    assert req.url.path.endswith("/gemini-3.8-flash-tts:generateContent")
    assert json.loads(req.content)["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"][
        "voiceName"] == "Sulafat"


def test_gemini_before_elevenlabs(tts, fake_http, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_ENABLED", "1")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-eleven")
    _gemini_ok(fake_http)
    assert voice.get_audio(run(voice.synthesize("Hi."))) == b"ID3gemini"
    assert not fake_http.calls_to("api.elevenlabs.io")


def test_explicit_openai_voice_or_model_skips_gemini(tts, fake_http):
    run(voice.synthesize("Hi.", voice="nova"))
    run(voice.synthesize("Hi.", model="tts-1"))
    assert not fake_http.calls_to(HOST) and len(tts) == 2


# ---- falling back ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("how", ["429", "timeout", "500", "no_audio"])
def test_failure_speaks_openai_marks_fallback_and_pauses_gemini(tts, fake_http, how, monkeypatch):
    if how == "429":
        fake_http.add(HOST, "/v1beta/models/", status=429, json={"error": {"code": 429}})
    elif how == "timeout":
        fake_http.raise_for(HOST, "/v1beta/models/", httpx.ReadTimeout("slow"))
    elif how == "500":
        fake_http.add(HOST, "/v1beta/models/", status=500, text="boom")
    else:
        fake_http.add(HOST, "/v1beta/models/", json={"candidates": [{"content": {"parts": [{"text": "hm"}]}}]})
    aid = run(voice.synthesize("Take the next exit."))
    assert voice.get_audio(aid) == b"ID3openai" and voice.is_fallback(aid)
    assert tts[0]["input"] == "Take the next exit." and tts[0]["voice"] == voice.DEFAULT_VOICE
    assert not voice.gemini_active()  # paused: the next lines don't wait on Gemini
    aid2 = run(voice.synthesize("And the one after."))
    assert len(fake_http.calls_to(HOST)) == 1 and voice.is_fallback(aid2) and len(tts) == 2
    # after the pause Gemini is asked again
    monkeypatch.setattr(voice, "_gemini_down_until", 0.0)
    fake_http.routes.clear()
    _gemini_ok(fake_http)
    assert voice.get_audio(run(voice.synthesize("Back again."))) == b"ID3gemini"


def test_timeout_is_short(tts):
    assert voice.GEMINI_TTS_TIMEOUT_S <= 6 and voice.GEMINI_TTS_COOLDOWN_S == 60


def test_fallback_clip_is_not_pinned_as_gemini(tts, fake_http):
    fake_http.add(HOST, "/v1beta/models/", status=429, json={})
    aid = run(voice.synthesize("Fixed line."))
    assert voice.pin(aid, "gemini|key") == aid  # stays a temporary clip: the next request asks Gemini again


# ---- cache tag and style ---------------------------------------------------------------------------------------

def test_cache_tag_names_gemini_model_voice_gain_and_style(tts, monkeypatch):
    tag = voice.cache_tag()
    assert tag.startswith("gemini|gemini-3.8-flash-lite-tts|Achird|g")
    assert f"|s{voice.GEMINI_STYLE_TAG}" in tag
    monkeypatch.setenv("GEMINI_TTS_VOICE", "Sulafat")
    assert voice.cache_tag() != tag
    assert voice.cache_tag(voice="nova").startswith("openai|")  # an explicit OpenAI voice is OpenAI's clip
    monkeypatch.setenv("GEMINI_TTS_ENABLED", "0")
    assert voice.cache_tag().startswith("openai|")  # old OpenAI clips and Gemini clips never share a key


@pytest.mark.parametrize("text, style", [
    ("Heads up, a storm near Truckee.", "GEMINI_STYLE"),
    ("Слушай, у Труки штормовое предупреждение.", "GEMINI_STYLE_RU"),
    ("Слухай, біля Труккі штормове попередження, знайти тобі місце?", "GEMINI_STYLE_UK"),
    ("Нашёл Love's на I-80.", "GEMINI_STYLE_RU"),
])
def test_style_by_language(text, style):
    assert voice.gemini_style_for(text) == getattr(voice, style)
    assert voice.gemini_body(text, "Achird")["contents"][0]["parts"][0]["text"] == text


def test_styles_say_what_the_owner_picked():
    assert voice.GEMINI_STYLE.startswith("Say this like a relaxed friend riding along in the passenger seat")
    assert "не как диктор и не как навигатор" in voice.GEMINI_STYLE_RU and "по-американски" in voice.GEMINI_STYLE_RU
    assert "не як диктор і не як навігатор" in voice.GEMINI_STYLE_UK and "по-американськи" in voice.GEMINI_STYLE_UK


# ---- audio -----------------------------------------------------------------------------------------------------

def test_wav_and_raw_pcm_both_become_mp3():
    wav = _wav(0.5)
    assert voice.audio_seconds(wav) == pytest.approx(0.5) and voice.audio_seconds(wav[44:]) == pytest.approx(0.5)
    for audio in (wav, wav[44:]):  # Gemini 3.8 answers a WAV; older TTS models raw PCM s16le
        mp3 = run(voice.pcm_to_mp3(audio))
        assert mp3 and (mp3[:3] == b"ID3" or mp3[0] == 0xFF)


# ---- spend -----------------------------------------------------------------------------------------------------

def test_gemini_tts_has_its_own_spend_line(tts, fake_http, monkeypatch):
    monkeypatch.setenv("GEMINI_CALLS_PER_DAY", "1")
    spend._pending[(quota.day(), "calls:gemini")] = 1  # Maps grounding already used its whole cap today
    _gemini_ok(fake_http, usage={"promptTokenCount": 40, "candidatesTokenCount": 50})
    aid = run(voice.synthesize("Hi there."))
    assert voice.get_audio(aid) == b"ID3gemini"  # the grounding cap does not stop the voice
    inp, out = spend.GEMINI_TTS_PER_M
    assert spend.calls_today("gemini_tts") == 1 and spend.calls_today("gemini") == 1
    assert spend._value("usd:gemini_tts") == pytest.approx((40 * inp + 50 * out) / 1e6)
    assert spend._value("usd:gemini") == 0
    assert spend.health()["calls_today"] == {"gemini": 1, "here": 0, "gemini_tts": 1}


def test_failed_call_is_counted_but_costs_nothing(tts, fake_http):
    fake_http.add(HOST, "/v1beta/models/", status=429, json={})
    run(voice.synthesize("Hi."))
    assert spend.calls_today("gemini_tts") == 1 and spend._value("usd:gemini_tts") == 0


def test_price_from_audio_seconds_without_usage():
    inp, out = spend.GEMINI_TTS_PER_M
    assert spend.price_gemini_tts(4.0, None, 400) == pytest.approx((100 * inp + 4 * 25 * out) / 1e6)
    assert spend.GEMINI_TTS_TOKENS_PER_S == 25
    # one sentence of about 3 s: a fraction of a cent
    assert spend.price_gemini_tts(3.0, None, 300) < 0.001


def test_cheap_voice_stage_switches_gemini_to_openai(tts, fake_http, monkeypatch):
    monkeypatch.setenv("DAILY_USD_CAP", "1")
    spend._pending[(quota.day(), "usd:openai")] = 0.99
    assert voice.gemini_voice() == "Achird"
    spend._pending[(quota.day(), "usd:openai")] = 1.0
    assert voice.gemini_voice() is None and voice.provider() == "openai" and voice.cache_tag().startswith("openai|")
    aid = run(voice.synthesize("Hi."))
    assert voice.get_audio(aid) == b"ID3openai" and not fake_http.calls_to(HOST)
    assert not voice.is_fallback(aid)  # the planned voice at this stage, cached as OpenAI's


def test_health_shows_gemini_tts(api_client, monkeypatch):
    monkeypatch.setenv("GEMINI_TTS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    spend.record("gemini_tts", 0.0, calls=3)
    body = api_client.get("/api/health").json()
    assert body["calls_today"]["gemini_tts"] == 3 and body["providers"]["tts"] == "gemini"


# ---- the first clip (server._speak) ----------------------------------------------------------------------------

@pytest.fixture
def speak_calls(monkeypatch):
    import server
    calls = []

    async def synth(text, *a, **k):
        calls.append(("whole", text))
        return "a" * 32

    def later(text, previous_text=None):
        calls.append(("later", text))
        return "b" * 32

    def stream(text, previous_text=None):
        calls.append(("stream", text))
        return "c" * 32

    monkeypatch.setattr(voice, "synthesize", synth)
    monkeypatch.setattr(voice, "synthesize_later", later)
    monkeypatch.setattr(voice, "synthesize_stream", stream)
    monkeypatch.setenv("TTS_STREAM", "1")
    monkeypatch.setenv("TTS_CLAUSE_SPLIT", "0")
    return server, calls


def _res(reply):
    return AgentResponse(status="chat", transcript="hi", reply=reply, source="llm")


def test_gemini_first_clip_is_whole_and_short(speak_calls, monkeypatch):
    server, calls = speak_calls
    monkeypatch.setenv("GEMINI_TTS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setattr(voice, "_gemini_down_until", 0.0)
    res = run(server._speak(_res("Here's another way via I-80 and CA-89, 4 h 38 min, 204 miles."), True))
    # no OpenAI stream (one voice for the whole reply); cut at the clause even with TTS_CLAUSE_SPLIT=0
    assert ("whole", "Here's another way via I-80 and CA-89,") in calls
    assert ("later", "4 h 38 min, 204 miles.") in calls and not [c for c in calls if c[0] == "stream"]
    assert res.audio_url and res.audio_url_2


def test_short_first_sentence_stays_whole(speak_calls, monkeypatch):
    server, calls = speak_calls
    monkeypatch.setenv("GEMINI_TTS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setattr(voice, "_gemini_down_until", 0.0)
    run(server._speak(_res("Alright, got one. Roady's Truck Stop, right off West Capitol Ave."), True))
    assert calls[-1] == ("whole", "Alright, got one.")


def test_paused_gemini_streams_openai_as_before(speak_calls, monkeypatch):
    server, calls = speak_calls
    monkeypatch.setenv("GEMINI_TTS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setattr(voice, "_gemini_down_until", voice.time.monotonic() + 60)  # paused after a failure
    run(server._speak(_res("Sure thing. The Pilot has showers."), True))
    assert ("stream", "Sure thing.") in calls
