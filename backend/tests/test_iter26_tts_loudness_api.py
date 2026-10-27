# Iteration 26: end-to-end loudness of OpenAI-fallback TTS via /api/speak, /api/agent and /api/voice/audio
import asyncio
import os
import subprocess
import time
import uuid

import imageio_ffmpeg
import pytest
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", ".env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
FF = imageio_ffmpeg.get_ffmpeg_exe()
BASE = (os.environ.get("EXPO_BACKEND_URL") or os.environ.get("EXPO_PUBLIC_BACKEND_URL")).rstrip("/")


def _stats(mp3: bytes) -> dict:
    out = subprocess.run([FF, "-hide_banner", "-i", "pipe:0", "-af", "loudnorm=print_format=summary", "-f", "null", "-"],
                         input=mp3, capture_output=True).stderr.decode()
    val = lambda k: float(next(l for l in out.splitlines() if k in l).split(":")[1].split()[0])  # noqa: E731
    import re
    times = re.findall(r"time=(\d+):(\d+):([\d.]+)", out)
    dur = (int(times[-1][0]) * 3600 + int(times[-1][1]) * 60 + float(times[-1][2])) if times else None
    return {"lufs": val("Input Integrated"), "peak": val("Input True Peak"), "dur": dur, "log": out}


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    r = sess.post(f"{BASE}/api/auth/access-code", json={"code": "TRUCK2026"}, timeout=20)
    assert r.status_code == 200, r.text
    sess.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return sess


# /api/speak: unique text so no cache hit -> fresh synthesis through fallback
def test_speak_loud_valid_mp3(s):
    text = f"TEST loudness check {uuid.uuid4().hex[:6]}. The nearest truck stop is about three miles ahead on your right, with diesel and showers."
    t0 = time.time()
    r = s.post(f"{BASE}/api/speak", json={"text": text}, timeout=60)
    elapsed = time.time() - t0
    print("speak latency", round(elapsed, 2))
    assert r.status_code == 200, r.text
    url = r.json()["audio_url"]
    a = requests.get(f"{BASE}{url}", timeout=30)
    assert a.status_code == 200
    assert a.headers["content-type"].startswith("audio/mpeg")
    st = _stats(a.content)
    print("speak stats", st["lufs"], st["peak"], st["dur"])
    assert st["lufs"] >= -19, st["lufs"]
    assert st["peak"] <= 0.5, st["peak"]
    assert st["dur"] and st["dur"] >= 4, st["dur"]  # ~20 words should be > 4s
    # Range request
    rg = requests.get(f"{BASE}{url}", headers={"Range": "bytes=0-1023"}, timeout=30)
    assert rg.status_code == 206
    assert len(rg.content) == 1024
    assert rg.headers["content-range"].startswith("bytes 0-1023/")
    assert rg.content == a.content[:1024]


# /api/speak without auth must be rejected
def test_speak_requires_auth():
    r = requests.post(f"{BASE}/api/speak", json={"text": "hello"}, timeout=20,
                      headers={"Authorization": "Bearer invalid"})  # conftest injects a token by default
    assert r.status_code in (401, 403)


# /api/agent typed text with speak=true -> loud audio
def test_agent_speak_loud(s):
    r = s.post(f"{BASE}/api/agent", params={"speak": "true"},
               json={"text": "I need fuel", "lat": 38.58, "lng": -121.49}, timeout=90)
    assert r.status_code == 200, r.text
    d = r.json()
    print("agent keys", list(d.keys()), str(d.get("reply") or d.get("text"))[:200])
    url = d.get("audio_url")
    assert url, d
    a = requests.get(f"{BASE}{url}", timeout=60)
    assert a.status_code == 200
    st = _stats(a.content)
    print("agent stats", st["lufs"], st["peak"], st["dur"])
    assert st["lufs"] >= -19
    assert st["peak"] <= 0.5
    if d.get("audio_url_2"):
        b = requests.get(f"{BASE}{d['audio_url_2']}", timeout=60)
        assert b.status_code == 200
        st2 = _stats(b.content)
        print("agent part2 stats", st2["lufs"], st2["peak"], st2["dur"])
        assert st2["lufs"] >= -19


# louder() on garbage / empty input returns input unchanged
def test_louder_garbage_and_empty():
    from services.voice import louder
    assert asyncio.run(louder(b"garbage")) == b"garbage"
    assert asyncio.run(louder(b"")) == b""


# louder() latency on a typical clip
def test_louder_latency():
    from openai import AsyncOpenAI
    from services.voice import louder
    raw = asyncio.run(AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"]).audio.speech.create(
        input="Heads up, weigh station open in two miles, keep right.", model="tts-1", voice="sage")).content
    t0 = time.time()
    out = asyncio.run(louder(raw))
    dt = time.time() - t0
    print("louder ms", round(dt * 1000))
    raw_d, out_d = _stats(raw)["dur"], _stats(out)["dur"]
    print("dur raw/out", raw_d, out_d)
    assert dt < 0.5
    assert abs(raw_d - out_d) < 0.3
