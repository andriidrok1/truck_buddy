"""Shared test setup.

Two kinds of tests live here:
- `tests/offline/` (and the files in OFFLINE_FILES): no network, no keys. These run in CI with
  `python -m pytest -n 0 -m "not live"`.
- everything else directly in `tests/`: live tests against a running backend. They need
  EXPO_PUBLIC_BACKEND_URL (or frontend/.env) and ACCESS_CODE, and are marked `live`
  automatically; without a backend URL they are skipped, not failed.
- `tests/smoke/`: the nightly smoke against the deployed backend, marked `smoke`.
"""
import io
import math
import os
import struct
import wave
from pathlib import Path

import pytest
import requests

TESTS_DIR = Path(__file__).resolve().parent
_FRONT_ENV = TESTS_DIR.parents[1] / "frontend" / ".env"
if not os.environ.get("EXPO_PUBLIC_BACKEND_URL") and _FRONT_ENV.exists():
    for line in _FRONT_ENV.read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "EXPO_PUBLIC_BACKEND_URL":
            os.environ["EXPO_PUBLIC_BACKEND_URL"] = v.strip().strip('"')
os.environ.setdefault("EXPO_BACKEND_URL", os.environ.get("EXPO_PUBLIC_BACKEND_URL", ""))

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")

# Files directly in tests/ that run without a backend (pure functions or monkeypatched services).
OFFLINE_FILES = {
    "test_breakdown.py",
    "test_fuel_prices.py",
    "test_iter11_speech_text.py",
    "test_iter15_controls.py",
    "test_iter17_approach.py",
    "test_iter18_side_geometry.py",
    "test_iter19_demo_places.py",
    "test_iter20_parking_cache.py",
    "test_iter22_addresses.py",
    "test_iter23_fuel_stop_nearby.py",
    "test_iter24_places_filter.py",
    "test_rate_limit.py",
    "test_stt_timeout.py",
}

# Individual tests inside OFFLINE_FILES that still need the network (real geocoder, real LLM).
LIVE_TESTS = {"test_geocoder_stays_in_california"}

_live_selected = False


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs a running backend (EXPO_PUBLIC_BACKEND_URL + ACCESS_CODE)")
    config.addinivalue_line("markers", "smoke: nightly smoke against the deployed backend")


def _is_live_file(p: Path) -> bool:
    if p.parent == TESTS_DIR / "smoke":
        return True
    return p.parent == TESTS_DIR and p.name.startswith("test_") and p.name not in OFFLINE_FILES


def pytest_ignore_collect(collection_path, config):
    """Live test modules read the backend URL at import time, so without one they must not even be imported."""
    p = Path(str(collection_path))
    if p.suffix != ".py" or not _is_live_file(p):
        return None
    if not BASE_URL or config.option.markexpr.replace(" ", "") == "notlive":
        return True
    return None


def pytest_collection_modifyitems(config, items):
    """Mark live/smoke tests by location; skip them when there is no backend to talk to."""
    global _live_selected
    skip = pytest.mark.skip(reason="no EXPO_PUBLIC_BACKEND_URL: live test skipped")
    for item in items:
        p = Path(str(item.fspath))
        if p.parent == TESTS_DIR / "smoke":
            item.add_marker("smoke")
            item.add_marker("live")
        elif p.parent == TESTS_DIR and (p.name not in OFFLINE_FILES or item.originalname in LIVE_TESTS):
            item.add_marker("live")
        if item.get_closest_marker("live"):
            if BASE_URL:
                _live_selected = True
            else:
                item.add_marker(skip)
    if _live_selected and config.option.markexpr != "not live":
        _auth_everything()


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
    if not BASE_URL or not _live_selected:
        return
    for path, text in SPEECH.items():
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            _speak_to(path, text)
