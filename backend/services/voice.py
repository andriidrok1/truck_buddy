"""Speech in / speech out: Whisper (STT); TTS via ElevenLabs (primary) or OpenAI tts-1 (fallback)."""
import asyncio
import io
import time
import logging
import os
import re
import uuid
from collections import OrderedDict
from typing import Optional

from emergentintegrations.llm.openai import OpenAISpeechToText, OpenAITextToSpeech

from services.http import client

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
FALLBACK_MODEL = "tts-1"  # OpenAI fallback when ElevenLabs is off or fails

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVEN_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel", ElevenLabs premade voice
ELEVEN_BODY = {"model_id": "eleven_flash_v2_5", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": 1.05}}
ELEVEN_TIMEOUT_S = 8
ELEVEN_COOLDOWN_S = 60  # after a failure, skip ElevenLabs for a while (no 8 s waits, no mixed voices)
_eleven_down_until = 0.0
_FALLBACK_IDS: "OrderedDict[str, None]" = OrderedDict()  # audio ids spoken by the fallback


def eleven_voice() -> Optional[str]:
    """ElevenLabs voice id when the key is set, else None (OpenAI is used)."""
    if not os.environ.get("ELEVENLABS_API_KEY"):
        return None
    return os.environ.get("ELEVENLABS_VOICE_ID") or ELEVEN_DEFAULT_VOICE


def provider() -> str:
    return "elevenlabs" if eleven_voice() else "openai"


def cache_tag(voice: Optional[str] = None, model: Optional[str] = None) -> str:
    """Identifies who speaks a cached line: provider + voice (changes invalidate /speak cache)."""
    if not voice and not model and eleven_voice():
        return f"elevenlabs|{eleven_voice()}"
    return f"openai|{model or FALLBACK_MODEL}|{voice or DEFAULT_VOICE}"


def is_fallback(audio_id: str) -> bool:
    return audio_id in _FALLBACK_IDS


def _eleven_failed() -> None:
    global _eleven_down_until
    _eleven_down_until = time.monotonic() + ELEVEN_COOLDOWN_S


async def _eleven(text: str, voice_id: str) -> Optional[bytes]:
    if time.monotonic() < _eleven_down_until:
        return None
    try:
        r = await client().post(
            ELEVEN_URL.format(voice_id=voice_id),
            params={"output_format": "mp3_22050_32"},
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Accept": "audio/mpeg"},
            json={"text": text, **ELEVEN_BODY},
            timeout=ELEVEN_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("ElevenLabs TTS failed, falling back to OpenAI: %s", e)
        _eleven_failed()
        return None
    if r.status_code != 200 or not r.content:
        logger.warning("ElevenLabs TTS %s, falling back to OpenAI: %s", r.status_code, r.text[:200])
        _eleven_failed()
        return None
    return r.content
# gpt-4o-mini-tts takes a speaking-style instruction (tts-1 ignores it).
INSTRUCTIONS = "Calm, warm, confident co-pilot talking to a truck driver on the road. Natural pace, clear and friendly."


async def synthesize(text: str, voice: Optional[str] = None, model: Optional[str] = None) -> Optional[str]:
    """Returns an id for GET /api/voice/audio/{id}, or None if TTS failed.
    ElevenLabs first (when configured and no explicit OpenAI voice/model is asked), then OpenAI."""
    text = text[:4000]
    audio = None
    eleven = eleven_voice() if not voice and not model else None
    if eleven:
        audio = await _eleven(text, eleven)
    fell_back = bool(eleven) and audio is None
    if audio is None:
        model = model or FALLBACK_MODEL
        try:
            tts = OpenAITextToSpeech(api_key=os.environ["EMERGENT_LLM_KEY"])
            extra = {"instructions": INSTRUCTIONS} if model == "gpt-4o-mini-tts" else {}
            audio = await tts.generate_speech(text=text, model=model, voice=voice or DEFAULT_VOICE, **extra)
        except Exception:
            logger.exception("TTS failed")
            return None
    audio_id = uuid.uuid4().hex
    _AUDIO_CACHE[audio_id] = audio
    while len(_AUDIO_CACHE) > _CACHE_MAX:
        _AUDIO_CACHE.popitem(last=False)
    if fell_back:
        _FALLBACK_IDS[audio_id] = None
        while len(_FALLBACK_IDS) > _CACHE_MAX:
            _FALLBACK_IDS.popitem(last=False)
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
