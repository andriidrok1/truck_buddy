"""Shared test setup: backend URL from frontend/.env and generated audio fixtures (no binaries in the repo)."""
import io
import math
import os
import struct
import wave
from pathlib import Path

import pytest
import requests

_FRONT_ENV = Path(__file__).resolve().parents[2] / "frontend" / ".env"
if not os.environ.get("EXPO_PUBLIC_BACKEND_URL") and _FRONT_ENV.exists():
    for line in _FRONT_ENV.read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "EXPO_PUBLIC_BACKEND_URL":
            os.environ["EXPO_PUBLIC_BACKEND_URL"] = v.strip().strip('"')
os.environ.setdefault("EXPO_BACKEND_URL", os.environ.get("EXPO_PUBLIC_BACKEND_URL", ""))

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")


def _auth_everything() -> None:
    """The API needs the access-code token: every requests call in the tests sends it (unless set)."""
    code = os.environ.get("ACCESS_CODE")
    if not code:
        pytest.exit("ACCESS_CODE env is required (the backend's access code; never committed)", returncode=2)
    token = requests.post(f"{BASE_URL}/api/auth/access-code", json={"code": code}, timeout=30).json()["access_token"]
    orig = requests.Session.request

    def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", None) or {})
        headers.setdefault("Authorization", f"Bearer {token}")
        return orig(self, method, url, headers=headers, **kw)

    requests.Session.request = request


if BASE_URL:  # pure unit tests (test_rate_limit, test_stt_timeout, ...) run without a backend
    _auth_everything()

# Speech fixtures used by voice tests: path -> phrase (spoken by our own /api/speak TTS, then fed to Whisper)
SPEECH = {
    "/tmp/q.mp3": "I need fuel.",
    "/tmp/reno.mp3": "Take me to Reno, Nevada.",
    "/tmp/onb_name.mp3": "My name is Mike.",
    "/tmp/onb_truck.mp3": "Thirteen foot six, fifty three foot trailer, eighty thousand pounds, no hazmat.",
    "/tmp/onb_prefs.mp3": "Avoid tolls. I like Love's and Pilot. Remind me to take a break every four hours.",
}


def tone_wav(seconds: float = 0.5, freq: float = 440.0, rate: int = 16000) -> bytes:
    """Short mono sine WAV for tests that only need a valid audio upload."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate)))
                               for i in range(int(seconds * rate))))
    return buf.getvalue()


def _speak_to(path: str, text: str) -> None:
    r = requests.post(f"{BASE_URL}/api/speak", json={"text": text}, timeout=90)
    r.raise_for_status()
    a = requests.get(f"{BASE_URL}{r.json()['audio_url']}", timeout=60)
    a.raise_for_status()
    tmp = f"{path}.{os.getpid()}.part"
    Path(tmp).write_bytes(a.content)
    os.replace(tmp, path)  # atomic: safe with two xdist workers


@pytest.fixture(scope="session", autouse=True)
def speech_fixtures():
    if not BASE_URL:
        return
    for path, text in SPEECH.items():
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            _speak_to(path, text)
