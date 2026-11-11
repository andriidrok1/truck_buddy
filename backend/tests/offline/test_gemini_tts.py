"""Gemini TTS as the copilot voice (GEMINI_TTS_ENABLED=1): provider order, the fall back to OpenAI with a pause on a
429 / timeout / bad reply, the cache tag, the style by language, its own spend line (gemini_tts) apart from the
Maps grounding call cap, the cheap_voice stage, and the first clip in server._speak."""
import asyncio
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


# ---- streaming (synthesize_stream: Gemini SSE) -----------------------------------------------------------------

PCM = [b"\x01\x00" * 2400, b"\x02\x00" * 4800, b"\x03\x00" * 2400]  # 0.1 s, 0.2 s, 0.1 s


def _sse(chunks, usage=None, delay=0.0, fail_after=None):
    """A streamGenerateContent?alt=sse body: one `data:` event per PCM chunk, usageMetadata in the last one."""
    async def body():
        for i, c in enumerate(chunks):
            if fail_after is not None and i >= fail_after:
                raise httpx.ReadError("connection reset")
            if delay:
                await asyncio.sleep(delay)
            event = {"candidates": [{"content": {"parts": [{"inlineData": {
                "mimeType": "audio/l16; rate=24000; channels=1", "data": base64.b64encode(c).decode()}}]}}]}
            if usage is not None and i == len(chunks) - 1:
                event["usageMetadata"] = usage
            yield f"data: {json.dumps(event)}\r\n\r\n".encode()

    return lambda request: httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"},
                                          request=request)


@pytest.fixture
def streams(tts, monkeypatch):
    """Both streams with a pass-through gain pipe (ffmpeg skipped); OpenAI's stream canned."""
    openai_streamed = []

    async def passthrough(src, out, input_fmt=voice.MP3_IN, gain_db=0):
        try:
            async for c in src:
                await out.put(c)
        except Exception as e:  # noqa: BLE001 - like the real pipe: the error first, then the end
            await out.put(e)
        await out.put(None)

    async def openai_stream(text):
        openai_streamed.append(text)
        yield b"ID3openai-stream"

    monkeypatch.setattr(voice, "_gain_pipe", passthrough)
    monkeypatch.setattr(voice, "_openai_stream", openai_stream)
    voice._STREAMS.clear()
    return openai_streamed


def _stream(text="Alright, got one."):
    async def flow():
        aid = voice.synthesize_stream(text)
        head = await voice.first_chunk(aid)
        data = b"".join([c async for c in voice.stream_chunks(aid)])
        return aid, head, data
    return run(flow())


def test_stream_speaks_gemini_sse(streams, fake_http):
    fake_http.add(HOST, "/v1beta/models/", fn=_sse(PCM, usage={"promptTokenCount": 60, "candidatesTokenCount": 10}))
    aid, head, data = _stream()
    assert head == PCM[0][:1] and data == b"".join(PCM) and streams == []
    req = fake_http.calls_to(HOST)[0]
    assert req.url.path == "/v1beta/models/gemini-3.8-flash-lite-tts:streamGenerateContent"
    assert req.url.params["alt"] == "sse" and req.headers["x-goog-api-key"] == "test-gemini"
    part = json.loads(req.content)["contents"][0]["parts"][0]
    assert part == {"text": "Alright, got one.", "speech_metadata": {"style": voice.GEMINI_STYLE}}
    inp, out = spend.GEMINI_TTS_PER_M
    assert spend.calls_today("gemini_tts") == 1 and spend.calls_today("gemini") == 0
    assert spend._value("usd:gemini_tts") == pytest.approx((60 * inp + 10 * out) / 1e6)
    assert voice.get_audio(aid) == b"".join(PCM) and voice.gemini_active()


def test_stream_cost_from_pcm_length_without_usage(streams, fake_http):
    fake_http.add(HOST, "/v1beta/models/", fn=_sse(PCM))
    _stream("Hi.")
    inp, out = spend.GEMINI_TTS_PER_M
    chars = len("Hi.") + len(voice.GEMINI_STYLE)
    assert spend._value("usd:gemini_tts") == pytest.approx((chars / 4 * inp + 0.4 * 25 * out) / 1e6)


def test_stream_429_pauses_gemini_and_openai_streams(streams, fake_http):
    fake_http.add(HOST, "/v1beta/models/", status=429, json={"error": {"code": 429}})
    aid, head, data = _stream()
    assert data == b"ID3openai-stream" and streams == ["Alright, got one."]
    assert not voice.gemini_active() and spend.calls_today("gemini_tts") == 1
    assert spend._value("usd:gemini_tts") == 0
    _stream("Next line.")  # paused: straight to OpenAI, Gemini not asked
    assert len(fake_http.calls_to(HOST)) == 1 and streams[-1] == "Next line."


def test_stream_too_slow_for_the_first_chunk_falls_back(streams, fake_http, monkeypatch):
    monkeypatch.setattr(voice, "STREAM_FIRST_CHUNK_S", 0.05)
    fake_http.add(HOST, "/v1beta/models/", fn=_sse(PCM, delay=0.5))
    aid, head, data = _stream()
    assert data == b"ID3openai-stream" and not voice.gemini_active()


def test_stream_cut_midway_keeps_what_arrived(streams, fake_http):
    fake_http.add(HOST, "/v1beta/models/", fn=_sse(PCM, fail_after=1))

    async def flow():
        aid = voice.synthesize_stream("Alright, got one.")
        buf = voice._STREAMS[aid]
        data = b"".join([c async for c in voice.stream_chunks(aid)])
        return data, buf.error

    data, error = run(flow())
    assert data == PCM[0] and error == "stream cut off" and streams == []  # no second voice midway
    assert not voice.gemini_active()
    assert spend._value("usd:gemini_tts") > 0  # what was made is paid for


def test_both_streams_fail_then_one_shot(streams, fake_http, monkeypatch):
    fake_http.add(HOST, "/v1beta/models/", status=503, text="busy")

    async def broken(text):
        raise RuntimeError("openai down")
        yield b""  # pragma: no cover

    monkeypatch.setattr(voice, "_openai_stream", broken)
    aid, head, data = _stream()
    assert data == b"ID3openai" and voice.get_audio(aid) == b"ID3openai"  # OpenAI's whole clip, same id


def test_stream_through_real_ffmpeg_gives_mp3(tts, fake_http):
    voice._STREAMS.clear()
    tone = b"".join(int(8000 * ((i // 20) % 2 * 2 - 1)).to_bytes(2, "little", signed=True) for i in range(12000))
    fake_http.add(HOST, "/v1beta/models/", fn=_sse([tone[:12000], tone[12000:]]))
    aid, head, data = _stream()
    assert data and (data[:3] == b"ID3" or data[0] == 0xFF) and data != tone


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
    monkeypatch.setenv("GEMINI_TTS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setattr(voice, "_gemini_down_until", 0.0)
    return server, calls


def _res(reply):
    return AgentResponse(status="chat", transcript="hi", reply=reply, source="llm")


def test_gemini_first_clip_is_streamed_as_the_first_sentence(speak_calls):
    server, calls = speak_calls
    res = run(server._speak(_res("Here's another way via I-80 and CA-89, 4 h 38 min, 204 miles. Want it?"), True))
    # streamed: no forced cut at the comma, the first sentence goes as it is; the rest is made whole in parallel
    assert ("stream", "Here's another way via I-80 and CA-89, 4 h 38 min, 204 miles.") in calls
    assert ("later", "Want it?") in calls and not [c for c in calls if c[0] == "whole"]
    assert res.audio_url and res.audio_url_2


def test_without_stream_gemini_first_clip_is_whole_and_cut_at_the_clause(speak_calls, monkeypatch):
    server, calls = speak_calls
    monkeypatch.setenv("TTS_STREAM", "0")
    run(server._speak(_res("Here's another way via I-80 and CA-89, 4 h 38 min, 204 miles."), True))
    assert ("whole", "Here's another way via I-80 and CA-89,") in calls
    assert ("later", "4 h 38 min, 204 miles.") in calls and not [c for c in calls if c[0] == "stream"]


def test_clause_flag_still_cuts_a_streamed_first_clip(speak_calls, monkeypatch):
    server, calls = speak_calls
    monkeypatch.setenv("TTS_CLAUSE_SPLIT", "1")
    run(server._speak(_res("Alright, got one. Roady's Truck Stop, right off West Capitol Ave."), True))
    assert ("stream", "Alright, got one.") in calls


def test_paused_gemini_still_streams(speak_calls, monkeypatch):
    server, calls = speak_calls
    monkeypatch.setattr(voice, "_gemini_down_until", voice.time.monotonic() + 60)  # paused after a failure
    run(server._speak(_res("Sure thing. The Pilot has showers."), True))
    assert ("stream", "Sure thing.") in calls  # synthesize_stream picks OpenAI itself
