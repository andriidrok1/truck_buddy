"""OpenAI TTS model selection: gpt-4o-mini-tts by default with the speaking-style instruction,
tts-1 via OPENAI_TTS_MODEL without it; cache tags differ so pinned clips regenerate on a switch."""
from types import SimpleNamespace

import pytest
from conftest import run

from services import voice


class _FakeSpeech:
    def __init__(self):
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=b"ID3fake-mp3")


@pytest.fixture
def tts(monkeypatch):
    speech = _FakeSpeech()
    fake = SimpleNamespace(audio=SimpleNamespace(speech=speech))
    monkeypatch.setattr(voice.llm, "client", lambda: fake)

    async def no_boost(mp3):
        return mp3

    monkeypatch.setattr(voice, "louder", no_boost)
    monkeypatch.delenv("ELEVENLABS_ENABLED", raising=False)
    return speech


def test_default_model_is_mini_tts_with_instructions(tts, monkeypatch):
    monkeypatch.delenv("OPENAI_TTS_MODEL", raising=False)
    audio_id = run(voice.synthesize("Take the next exit."))
    assert audio_id and voice.get_audio(audio_id) == b"ID3fake-mp3"
    call = tts.calls[0]
    assert call["model"] == "gpt-4o-mini-tts"
    assert call["instructions"] == voice.INSTRUCTIONS
    assert call["voice"] == voice.DEFAULT_VOICE and call["response_format"] == "mp3"


def test_env_switches_back_to_tts1_without_instructions(tts, monkeypatch):
    monkeypatch.setenv("OPENAI_TTS_MODEL", "tts-1")
    run(voice.synthesize("Take the next exit."))
    call = tts.calls[0]
    assert call["model"] == "tts-1" and "instructions" not in call


def test_explicit_model_wins_over_env(tts, monkeypatch):
    monkeypatch.setenv("OPENAI_TTS_MODEL", "tts-1")
    run(voice.synthesize("Hi.", model="gpt-4o-mini-tts"))
    assert tts.calls[0]["model"] == "gpt-4o-mini-tts" and "instructions" in tts.calls[0]


def test_cache_tag_follows_the_model(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_TTS_MODEL", raising=False)
    a = voice.cache_tag()
    monkeypatch.setenv("OPENAI_TTS_MODEL", "tts-1")
    b = voice.cache_tag()
    assert a != b and a.startswith("openai|gpt-4o-mini-tts|") and b.startswith("openai|tts-1|")
    assert voice.cache_tag(model="tts-1-hd").startswith("openai|tts-1-hd|")


def test_text_goes_through_the_speech_layer(tts):
    run(voice.synthesize("Exit 12 onto I-80 in 2 mi."))
    assert tts.calls[0]["input"] == "Exit 12 onto Interstate 80 in two miles."


def test_tts_failure_returns_none(monkeypatch):
    class Boom:
        async def create(self, **kw):
            raise RuntimeError("quota")

    monkeypatch.setattr(voice.llm, "client", lambda: SimpleNamespace(audio=SimpleNamespace(speech=Boom())))
    monkeypatch.delenv("ELEVENLABS_ENABLED", raising=False)
    assert run(voice.synthesize("Hi.")) is None
