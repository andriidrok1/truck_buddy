"""Speech in / speech out: Whisper (STT); TTS via ElevenLabs (primary) or OpenAI tts-1 (fallback)."""
import asyncio
import hashlib
import io
import time
import logging
import os
import re
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from emergentintegrations.llm.openai import OpenAISpeechToText, OpenAITextToSpeech

from services.http import client
from services.speech_text import spoken

logger = logging.getLogger("truckbuddy.voice")

NO_SPEECH_PROB = 0.6
NO_SPEECH_LOGPROB = -0.8  # a likely-silent segment is dropped only when Whisper is also unsure of the words
MIN_AVG_LOGPROB = -1.4
MAX_COMPRESSION = 2.4
# Domain words + address style, so Whisper writes chains correctly and numbers as digits.
PROMPT = ("Truck driver talking to a copilot. Take me to 2828 El Centro Rd, Sacramento, CA. "
          "Exit 12 on Interstate 80, I-5 south, Highway 99. Love's, Pilot, Flying J, TA, Petro, Sapp Bros, "
          "Kwik Trip, Buc-ee's, Motel 6, rest area, weigh station, 13 foot 6, 53 foot trailer, 80,000 pounds.")
# Whisper labels for non-speech sounds, e.g. "Beeping", "[siren]", "(music)".
NOISE_ONLY = re.compile(
    r"[\s\W]*(?:(?:beep(?:ing|s)?|siren(?:s)?|music|noise|static|buzz(?:ing)?|tone|silence|applause|"
    r"engine|horn|humming|ringing|wind)[\s\W]*)*",
    re.IGNORECASE,
)
_AUDIO_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_CACHE_MAX = 50


STT_MODEL = "gpt-4o-mini-transcribe"  # more accurate with accents/cab noise, faster; returns "" on silence


async def transcribe(data: bytes, filename: str) -> str:
    stt = OpenAISpeechToText(api_key=os.environ["EMERGENT_LLM_KEY"])
    try:
        buf = io.BytesIO(data)
        buf.name = filename
        res = await stt.transcribe(file=buf, model=STT_MODEL, response_format="json", language="en",
                                   prompt=PROMPT, temperature=0.0)
        return _clean((getattr(res, "text", "") or "").strip())
    except Exception as e:  # noqa: BLE001 - fall back to Whisper with its confidence guard
        logger.warning("stt %s failed, falling back to whisper-1: %s", STT_MODEL, e)
    buf = io.BytesIO(data)
    buf.name = filename
    res = await stt.transcribe(file=buf, model="whisper-1", response_format="verbose_json", language="en",
                               prompt=PROMPT)
    segments = getattr(res, "segments", None) or []
    # Whisper invents text on silence/noise; drop segments that are most likely not speech.
    if segments:
        def _get(s, key, default=None):
            return s.get(key, default) if isinstance(s, dict) else getattr(s, key, default)

        kept = []
        for s in segments:
            nsp, lp, cr = _get(s, "no_speech_prob", 0), _get(s, "avg_logprob", 0), _get(s, "compression_ratio", 0)
            seg = (_get(s, "text", "") or "").strip()
            if (nsp >= NO_SPEECH_PROB and lp < NO_SPEECH_LOGPROB) or lp < MIN_AVG_LOGPROB or cr > MAX_COMPRESSION:
                logger.info("stt dropped segment %r no_speech=%.2f logprob=%.2f compression=%.2f", seg, nsp, lp, cr)
            else:
                kept.append(seg)
        text = " ".join(kept).strip()
    else:
        text = (getattr(res, "text", "") or "").strip()
    return _clean(text)


def _clean(text: str) -> str:
    if NOISE_ONLY.fullmatch(text):
        if text:
            logger.info("stt dropped noise-only text %r", text)
        return ""
    if PROMPT_ECHO.search(text) or HALLUCINATION.search(text):  # prompt echo / Whisper's stock lines on noise
        logger.info("stt dropped hallucination %r", text)
        return ""
    return text


PROMPT_ECHO = re.compile(r"Truck driver talking to a copilot|Sapp Bros, Kwik Trip, Buc-ee", re.I)
# Lines Whisper is known to invent on road noise / silence (subtitle credits, video outros, URLs).
HALLUCINATION = re.compile(
    r"for more information,? visit|www\.|\.(?:gov|com|org)\b|thanks? (?:you )?for watching|please subscribe|"
    r"like and subscribe|subtitles? by|captions? by|amara\.org|transcribed by|see you in the next video",
    re.I,
)


VOICES = ("alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer")
DEFAULT_VOICE = "sage"
FALLBACK_MODEL = "tts-1"  # OpenAI fallback when ElevenLabs is off or fails

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVEN_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel", ElevenLabs premade voice
ELEVEN_MODEL = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")
ELEVEN_FORMAT = "mp3_44100_96"
# Conversational delivery: a bit less stable, some style, speaker boost.
ELEVEN_SETTINGS = {"stability": 0.35, "similarity_boost": 0.75, "style": 0.3, "use_speaker_boost": True, "speed": 1.0}
ELEVEN_TIMEOUT_S = 8
ELEVEN_COOLDOWN_S = 60  # after a failure, skip ElevenLabs for a while (no 8 s waits, no mixed voices)
_eleven_down_until = 0.0
_eleven_slots = asyncio.Semaphore(int(os.environ.get("ELEVENLABS_CONCURRENCY", "2")))
_FALLBACK_IDS: "OrderedDict[str, None]" = OrderedDict()  # audio ids spoken by the fallback


def eleven_voice() -> Optional[str]:
    """ElevenLabs voice id when the key is set, else None (OpenAI is used)."""
    if not os.environ.get("ELEVENLABS_API_KEY"):
        return None
    return os.environ.get("ELEVENLABS_VOICE_ID") or ELEVEN_DEFAULT_VOICE


def provider() -> str:
    return "elevenlabs" if eleven_voice() else "openai"


def cache_tag(voice: Optional[str] = None, model: Optional[str] = None, eleven_voice_id: Optional[str] = None) -> str:
    """Identifies who speaks a cached line: provider + voice (changes invalidate /speak cache)."""
    if not voice and not model and eleven_voice():
        return f"elevenlabs|{ELEVEN_MODEL}|{eleven_voice_id or eleven_voice()}"
    return f"openai|{model or FALLBACK_MODEL}|{voice or DEFAULT_VOICE}"


def is_fallback(audio_id: str) -> bool:
    return audio_id in _FALLBACK_IDS


def _eleven_failed() -> None:
    global _eleven_down_until
    _eleven_down_until = time.monotonic() + ELEVEN_COOLDOWN_S


async def _eleven(text: str, voice_id: str, previous_text: Optional[str] = None,
                  next_text: Optional[str] = None) -> Optional[bytes]:
    if time.monotonic() < _eleven_down_until:
        return None
    body = {"text": text, "model_id": ELEVEN_MODEL, "voice_settings": ELEVEN_SETTINGS,
            # Neighbouring sentences keep the intonation continuous across the two audio parts.
            **({"previous_text": previous_text} if previous_text else {}),
            **({"next_text": next_text} if next_text else {})}
    try:
        for attempt in range(2):
            async with _eleven_slots:  # stay under the plan's concurrent-request limit
                r = await client().post(
                    ELEVEN_URL.format(voice_id=voice_id),
                    params={"output_format": ELEVEN_FORMAT},
                    headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Accept": "audio/mpeg"},
                    json=body,
                    timeout=ELEVEN_TIMEOUT_S,
                )
            if r.status_code != 429 or attempt:
                break
            await asyncio.sleep(0.4)  # busy, not broken: retry once
    except Exception as e:  # noqa: BLE001
        logger.warning("ElevenLabs TTS failed, falling back to OpenAI: %s", e)
        _eleven_failed()
        return None
    if r.status_code == 429:
        logger.warning("ElevenLabs busy (429), using OpenAI for this line")
        return None  # no cooldown: the next line can use ElevenLabs again
    if r.status_code != 200 or not r.content:
        logger.warning("ElevenLabs TTS %s, falling back to OpenAI: %s", r.status_code, r.text[:200])
        _eleven_failed()
        return None
    return r.content


# gpt-4o-mini-tts takes a speaking-style instruction (tts-1 ignores it).
INSTRUCTIONS = "Calm, warm, confident co-pilot talking to a truck driver on the road. Natural pace, clear and friendly."


async def synthesize(text: str, voice: Optional[str] = None, model: Optional[str] = None,
                     previous_text: Optional[str] = None, next_text: Optional[str] = None,
                     eleven_voice_id: Optional[str] = None) -> Optional[str]:
    """Returns an id for GET /api/voice/audio/{id}, or None if TTS failed.
    ElevenLabs first (when configured and no explicit OpenAI voice/model is asked), then OpenAI."""
    text = spoken(text)[:4000]  # everything spoken goes through the text-for-speech layer
    previous_text = spoken(previous_text) if previous_text else None
    next_text = spoken(next_text) if next_text else None
    audio = None
    eleven = eleven_voice() if not voice and not model else None
    if eleven:
        audio = await _eleven(text, eleven_voice_id or eleven, previous_text, next_text)
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


_PIN_DIR = Path(__file__).resolve().parent.parent / ".tts_cache"


def pin(audio_id: str, key: str) -> str:
    """Stores a fixed line (fillers, onboarding) on disk under a stable id derived from its cache key."""
    data = _AUDIO_CACHE.get(audio_id)
    if data is None or audio_id in _FALLBACK_IDS:
        return audio_id
    stable = _stable_id(key)
    try:
        _PIN_DIR.mkdir(exist_ok=True)
        (_PIN_DIR / f"{stable}.mp3").write_bytes(data)
    except OSError as e:
        logger.warning("could not pin audio: %s", e)
        return audio_id
    _AUDIO_CACHE.pop(audio_id, None)
    _AUDIO_CACHE[stable] = data
    return stable


def _stable_id(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:32]


def pinned(key: str) -> Optional[str]:
    """Id of an already stored fixed line (no new TTS call after a restart), or None."""
    sid = _stable_id(key)
    return sid if get_audio(sid) is not None else None


def get_audio(audio_id: str) -> Optional[bytes]:
    data = _AUDIO_CACHE.get(audio_id)
    if data is None and re.fullmatch(r"[0-9a-f]{32}", audio_id or ""):
        f = _PIN_DIR / f"{audio_id}.mp3"
        if f.exists():
            data = f.read_bytes()
            _AUDIO_CACHE[audio_id] = data
    return data


_PENDING: dict[str, "asyncio.Task[Optional[str]]"] = {}


def synthesize_later(text: str, previous_text: Optional[str] = None) -> str:
    """Starts TTS in the background and returns the audio id right away (served once ready)."""
    audio_id = uuid.uuid4().hex

    async def run() -> Optional[str]:
        tmp = await synthesize(text, previous_text=previous_text)
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
