"""Speech in / speech out: Whisper (STT) and OpenAI TTS via the Emergent universal key."""
import asyncio
import io
import logging
import os
import re
import uuid
from collections import OrderedDict
from typing import Optional

from emergentintegrations.llm.openai import OpenAISpeechToText, OpenAITextToSpeech

logger = logging.getLogger("truckbuddy.voice")

NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.0
MAX_COMPRESSION = 2.4
# Whisper labels for non-speech sounds, e.g. "Beeping", "[siren]", "(music)".
NOISE_ONLY = re.compile(
    r"[\s\W]*(?:(?:beep(?:ing|s)?|siren(?:s)?|music|noise|static|buzz(?:ing)?|tone|silence|applause|"
    r"engine|horn|humming|ringing|wind)[\s\W]*)*",
    re.IGNORECASE,
)
_AUDIO_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_CACHE_MAX = 50


async def transcribe(data: bytes, filename: str) -> str:
    buf = io.BytesIO(data)
    buf.name = filename
    stt = OpenAISpeechToText(api_key=os.environ["EMERGENT_LLM_KEY"])
    res = await stt.transcribe(file=buf, model="whisper-1", response_format="verbose_json", language="en")
    segments = getattr(res, "segments", None) or []
    # Whisper invents text on silence/noise; drop segments that are most likely not speech.
    if segments:
        def _get(s, key, default=None):
            return s.get(key, default) if isinstance(s, dict) else getattr(s, key, default)

        kept = [
            _get(s, "text", "") for s in segments
            if _get(s, "no_speech_prob", 0) < NO_SPEECH_PROB
            and _get(s, "avg_logprob", 0) >= MIN_AVG_LOGPROB
            and _get(s, "compression_ratio", 0) <= MAX_COMPRESSION
        ]
        text = " ".join(t.strip() for t in kept).strip()
    else:
        text = (getattr(res, "text", "") or "").strip()
    if NOISE_ONLY.fullmatch(text):
        return ""
    return text


VOICES = ("alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer")
DEFAULT_VOICE = "sage"
DEFAULT_MODEL = "gpt-4o-mini-tts"  # warmer, natural tone (chosen by the owner)
# gpt-4o-mini-tts takes a speaking-style instruction (tts-1 ignores it).
INSTRUCTIONS = "Calm, warm, confident co-pilot talking to a truck driver on the road. Natural pace, clear and friendly."


async def synthesize(text: str, voice: Optional[str] = None, model: str = DEFAULT_MODEL) -> Optional[str]:
    """Returns an id for GET /api/voice/audio/{id}, or None if TTS failed."""
    try:
        tts = OpenAITextToSpeech(api_key=os.environ["EMERGENT_LLM_KEY"])
        extra = {"instructions": INSTRUCTIONS} if model == "gpt-4o-mini-tts" else {}
        audio = await tts.generate_speech(text=text[:4000], model=model, voice=voice or DEFAULT_VOICE, **extra)
    except Exception:
        logger.exception("TTS failed")
        return None
    audio_id = uuid.uuid4().hex
    _AUDIO_CACHE[audio_id] = audio
    while len(_AUDIO_CACHE) > _CACHE_MAX:
        _AUDIO_CACHE.popitem(last=False)
    return audio_id


def get_audio(audio_id: str) -> Optional[bytes]:
    return _AUDIO_CACHE.get(audio_id)


_PENDING: dict[str, "asyncio.Task[Optional[str]]"] = {}


def synthesize_later(text: str) -> str:
    """Starts TTS in the background and returns the audio id right away (served once ready)."""
    audio_id = uuid.uuid4().hex

    async def run() -> Optional[str]:
        tmp = await synthesize(text)
        if tmp:
            _AUDIO_CACHE[audio_id] = _AUDIO_CACHE.pop(tmp)
        return tmp

    task = asyncio.create_task(run())
    _PENDING[audio_id] = task
    task.add_done_callback(lambda _t: _PENDING.pop(audio_id, None))
    return audio_id


async def wait_audio(audio_id: str) -> Optional[bytes]:
    task = _PENDING.get(audio_id)
    if task is not None:
        await asyncio.shield(task)
    return get_audio(audio_id)
