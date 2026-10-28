# Probe: /api/speak latency vs raw OpenAI tts-1 + louder() cost (run manually: python tests/voice_latency_probe.py)
import asyncio
import os
import time
import uuid

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", ".env"))
BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")


async def raw(text):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from openai import AsyncOpenAI
    from services.voice import louder
    t0 = time.time()
    a = (await AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"]).audio.speech.create(input=text, model="tts-1", voice="sage")).content
    t1 = time.time()
    await louder(a)
    return t1 - t0, time.time() - t1


tok = requests.post(f"{BASE}/api/auth/access-code", json={"code": os.environ["ACCESS_CODE"]}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
for i in range(3):
    text = f"Probe {uuid.uuid4().hex[:4]}, weigh station open in two miles, keep right."
    t0 = time.time()
    r = requests.post(f"{BASE}/api/speak", json={"text": text}, headers=H, timeout=60)
    sp = time.time() - t0
    g, b = asyncio.run(raw(text + " again"))
    print(f"speak={sp:.2f}s status={r.status_code} raw_openai={g:.2f}s louder={b*1000:.0f}ms")
