# Iteration 25: OpenAI TTS fallback is boosted to ElevenLabs loudness (it was ~12 dB quieter on phones)
import asyncio
import os
import subprocess

import imageio_ffmpeg
import pytest
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
FF = imageio_ffmpeg.get_ffmpeg_exe()
BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")


def _lufs(mp3: bytes) -> tuple[float, float]:
    out = subprocess.run([FF, "-hide_banner", "-i", "pipe:0", "-af", "loudnorm=print_format=summary", "-f", "null", "-"],
                         input=mp3, capture_output=True).stderr.decode()
    val = lambda k: float(next(l for l in out.splitlines() if k in l).split(":")[1].split()[0])  # noqa: E731
    return val("Input Integrated"), val("Input True Peak")


def test_openai_tts_boosted():
    from emergentintegrations.llm.openai import OpenAITextToSpeech
    from services.voice import louder

    raw = asyncio.run(OpenAITextToSpeech(api_key=os.environ["EMERGENT_LLM_KEY"]).generate_speech(
        text="Alright, route to Reno is set, two hours and forty minutes.", model="tts-1", voice="sage"))
    before, _ = _lufs(raw)
    after, peak = _lufs(asyncio.run(louder(raw)))
    assert after - before >= 9, (before, after)
    assert -18 <= after <= -12, after
    assert peak <= 1.0, peak


def test_louder_returns_original_on_garbage():
    from services.voice import louder
    assert asyncio.run(louder(b"not audio")) == b"not audio"


@pytest.mark.skipif(not BASE, reason="no backend URL")
def test_speak_endpoint_audio_is_loud():
    r = requests.post(f"{BASE}/api/speak", json={"text": "The nearest truck stop is about three miles ahead on your right."},
                      timeout=60)
    assert r.status_code == 200
    audio = requests.get(f"{BASE}{r.json()['audio_url']}", timeout=30).content
    lufs, _ = _lufs(audio)
    assert lufs >= -19, lufs  # ElevenLabs ~-15, boosted OpenAI ~-15; raw OpenAI was ~-26
