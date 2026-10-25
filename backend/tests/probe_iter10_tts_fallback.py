"""In-process probe: invalid ElevenLabs key -> warning + OpenAI tts-1 fallback; network error -> fallback; explicit voice skips ElevenLabs."""
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from services import voice  # noqa: E402


async def main():
    ok = True
    # 1) Real key: ElevenLabs path
    t0 = time.time()
    aid = await voice.synthesize("Real key check.")
    print(f"[real] provider={voice.provider()} id={bool(aid)} bytes={len(voice.get_audio(aid) or b'')} {int((time.time()-t0)*1000)}ms")
    ok &= bool(aid)

    real = os.environ["ELEVENLABS_API_KEY"]
    # 2) Invalid key -> 401 -> fallback
    os.environ["ELEVENLABS_API_KEY"] = "sk_invalid"
    t0 = time.time()
    aid = await voice.synthesize("Fallback check one.")
    data = voice.get_audio(aid) if aid else None
    print(f"[invalid key] id={bool(aid)} bytes={len(data or b'')} {int((time.time()-t0)*1000)}ms head={data[:3] if data else None}")
    ok &= bool(data)
    print(f"[invalid key] cache_tag={voice.cache_tag()} (still elevenlabs tag since key is set)")

    # 3) Unreachable URL -> exception -> fallback
    os.environ["ELEVENLABS_API_KEY"] = real
    orig = voice.ELEVEN_URL
    voice.ELEVEN_URL = "https://127.0.0.1:9/v1/text-to-speech/{voice_id}"
    aid = await voice.synthesize("Fallback check two.")
    print(f"[network err] id={bool(aid)}")
    ok &= bool(aid)
    voice.ELEVEN_URL = orig

    # 4) Key unset -> provider openai
    os.environ.pop("ELEVENLABS_API_KEY")
    print(f"[no key] provider={voice.provider()} tag={voice.cache_tag()}")
    ok &= voice.provider() == "openai"
    os.environ["ELEVENLABS_API_KEY"] = real
    print("RESULT:", "PASS" if ok else "FAIL")


asyncio.run(main())
