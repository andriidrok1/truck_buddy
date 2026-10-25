"""Probe /api/voice with tone, silence and TTS-generated phrases (manual edge-case script)."""
import asyncio
import io
import math
import os
import struct
import wave

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
from emergentintegrations.llm.openai import OpenAITextToSpeech  # noqa: E402

URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/") + "/api/voice"


def wav(kind, secs=3, sr=16000):
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    frames = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / sr)) if kind == "tone" else 0)
                      for i in range(sr * secs))
    w.writeframes(frames); w.close()
    return buf.getvalue()


def post(name, data, mime, **fields):
    r = requests.post(URL, files={"audio": (name, data, mime)},
                      data={"lat": "38.5816", "lng": "-121.4944", **fields}, timeout=90)
    d = r.json()
    print(f"{name:18} {r.status_code} status={d.get('status')} transcript={d.get('transcript')!r} reply={d.get('reply')!r}")


async def main():
    for i in range(2):
        post(f"tone{i}.wav", wav("tone"), "audio/wav")
    post("silence.wav", wav("silence"), "audio/wav")
    tts = OpenAITextToSpeech(api_key=os.environ["EMERGENT_LLM_KEY"])
    for phrase, extra in [("How far is it?", {"active_place_id": "ts-capitol"}), ("Thanks a lot", {}),
                          ("I'm tired, I need to sleep", {})]:
        audio = await tts.generate_speech(text=phrase, model="tts-1", voice="alloy")
        post(phrase[:16] + ".mp3", audio, "audio/mpeg", **extra)


asyncio.run(main())
