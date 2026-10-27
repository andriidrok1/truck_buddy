"""Unit test (no server): a slow STT becomes SttTimeout quickly, without the whisper-1 fallback doubling the wait."""
import asyncio
import time
import types

import pytest

from services import voice


class _SlowTranscriptions:
    def __init__(self):
        self.calls = 0

    async def create(self, **_kw):
        self.calls += 1
        await asyncio.sleep(1.0)
        return types.SimpleNamespace(text="never")


def test_slow_stt_raises_timeout(monkeypatch):
    slow = _SlowTranscriptions()
    fake = types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=slow))
    monkeypatch.setattr(voice.llm, "client", lambda: fake)
    monkeypatch.setattr(voice, "STT_TIMEOUT_S", 0.05)
    t0 = time.monotonic()
    with pytest.raises(voice.SttTimeout):
        asyncio.run(voice.transcribe(b"\x00" * 100, "speech.m4a"))
    assert time.monotonic() - t0 < 0.5
    assert slow.calls == 1  # no fallback attempt after a timeout
