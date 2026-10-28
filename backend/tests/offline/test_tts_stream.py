"""Streamed TTS behind TTS_STREAM: bytes reach the app as they are generated; failures fall back to one-shot."""
import asyncio
from types import SimpleNamespace

import pytest
from conftest import run

from services import voice

SAC = {"lat": 38.5816, "lng": -121.4944}
CHUNKS = [b"ID3chunk-one", b"chunk-two", b"chunk-three"]


class _Resp:
    def __init__(self, chunks, delay=0.02, fail_after=None):
        self.chunks, self.delay, self.fail_after = chunks, delay, fail_after

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def iter_bytes(self, n):
        for i, c in enumerate(self.chunks):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("stream cut")
            await asyncio.sleep(self.delay)
            yield c


class _Speech:
    def __init__(self, chunks=CHUNKS, **kw):
        self.chunks, self.kw, self.calls, self.oneshot = chunks, kw, [], []
        self.with_streaming_response = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return _Resp(self.chunks, **self.kw)

    async def create(self, **kw):  # the one-shot path used by the fallback
        self.oneshot.append(kw)
        return SimpleNamespace(content=b"ID3one-shot")


@pytest.fixture
def speech(monkeypatch):
    def install(**kw):
        sp = _Speech(**kw)
        monkeypatch.setattr(voice.llm, "client", lambda: SimpleNamespace(audio=SimpleNamespace(speech=sp)))
        return sp

    async def passthrough(src, out):
        async for c in src:
            await out.put(c)
        await out.put(None)

    monkeypatch.setattr(voice, "_gain_pipe", passthrough)

    async def no_boost(mp3):
        return mp3

    monkeypatch.setattr(voice, "louder", no_boost)
    monkeypatch.delenv("ELEVENLABS_ENABLED", raising=False)
    voice._STREAMS.clear()
    return install


def test_chunks_arrive_progressively_then_land_in_the_cache(speech):
    sp = speech(delay=0.05)

    async def flow():
        aid = voice.synthesize_stream("Take the next exit, it's on the right.")
        assert voice.is_streaming(aid)
        head = await voice.first_chunk(aid)
        t_first = asyncio.get_event_loop().time()
        seen = []
        async for c in voice.stream_chunks(aid):
            seen.append((c, asyncio.get_event_loop().time() - t_first))
        return aid, head, seen

    aid, head, seen = run(flow())
    assert head == b"I" and [c for c, _ in seen] == CHUNKS
    assert seen[-1][1] > 0.08  # the last chunk came later than the first: it was streamed, not buffered
    assert not voice.is_streaming(aid) and voice.get_audio(aid) == b"".join(CHUNKS)
    call = sp.calls[0]
    assert call["stream_format"] == "audio" and call["model"] == "gpt-4o-mini-tts" and "instructions" in call
    assert call["input"] == "Take the next exit, it's on the right."


def test_failure_before_any_audio_falls_back_to_one_shot(speech):
    sp = speech(fail_after=0)

    async def flow():
        aid = voice.synthesize_stream("Hi there.")
        data = b"".join([c async for c in voice.stream_chunks(aid)])
        return aid, data

    aid, data = run(flow())
    assert data == b"ID3one-shot" and sp.oneshot and voice.get_audio(aid) == b"ID3one-shot"


def test_failure_mid_stream_keeps_what_arrived(speech):
    speech(fail_after=2)

    async def flow():
        aid = voice.synthesize_stream("Hi there.")
        return b"".join([c async for c in voice.stream_chunks(aid)])

    assert run(flow()) == b"".join(CHUNKS[:2])


def test_no_first_chunk_in_time_falls_back(speech, monkeypatch):
    sp = speech(delay=0.5)
    monkeypatch.setattr(voice, "STREAM_FIRST_CHUNK_S", 0.05)

    async def flow():
        aid = voice.synthesize_stream("Hi there.")
        return b"".join([c async for c in voice.stream_chunks(aid)])

    assert run(flow()) == b"ID3one-shot" and sp.oneshot


def test_audio_endpoint_serves_a_live_stream(api_client, agent_sandbox, speech, monkeypatch):
    speech(delay=0.05)
    monkeypatch.setenv("TTS_STREAM", "1")
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure thing, no problem at all today."}
    r = api_client.post("/api/agent?speak=true", json={"text": "hi", **SAC})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["timings"]["tts"] == 0 and body["audio_url"]
    aid = body["audio_url"].rsplit("/", 1)[-1]
    probe = api_client.get(body["audio_url"], headers={"Range": "bytes=0-0"})
    assert probe.status_code == 206 and probe.headers["content-range"] == "bytes 0-0/*" and probe.content == b"I"
    full = api_client.get(body["audio_url"])
    assert full.status_code == 200 and full.content == b"".join(CHUNKS)
    assert "content-length" not in full.headers or full.headers.get("accept-ranges") == "none"
    # once finished: the normal range path serves the cached bytes
    done = api_client.get(body["audio_url"], headers={"Range": "bytes=0-3"})
    assert done.status_code == 206 and done.content == b"ID3c" and voice.get_audio(aid)


def test_flag_off_uses_the_one_shot_path(api_client, agent_sandbox, speech, monkeypatch):
    sp = speech()
    monkeypatch.setenv("TTS_STREAM", "0")
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure."}
    r = api_client.post("/api/agent?speak=true", json={"text": "hi", **SAC})
    assert r.status_code == 200 and sp.oneshot and not sp.calls
    assert api_client.get(r.json()["audio_url"]).content == b"ID3one-shot"


def test_stream_speech_endpoint_rejects_unknown_id(api_client):
    assert api_client.get("/api/voice/audio/00000000000000000000000000000000").status_code == 404
