"""Speech in / speech out: Whisper (STT); TTS via Gemini TTS or ElevenLabs (when enabled), else OpenAI
gpt-4o-mini-tts."""
import asyncio
import base64
import hashlib
import io
import json
import time
import logging
import math
import os
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import imageio_ffmpeg

from services import llm, spend
from services.logs import said
from services.settings import settings
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
STT_TIMEOUT_S = 12.0  # a slow STT must become a spoken "try again", not "I didn't catch that"


class SttTimeout(Exception):
    """Speech recognition did not answer within STT_TIMEOUT_S."""


@dataclass
class Transcript:
    text: str
    confidence: Optional[float] = None  # 0..1, exp(mean token logprob); None when the model gave none


def confidence_of(logprobs) -> Optional[float]:
    """exp(mean logprob) over the tokens the model reported, or None without usable logprobs."""
    vals = []
    for lp in logprobs if isinstance(logprobs, list) else []:
        v = lp.get("logprob") if isinstance(lp, dict) else getattr(lp, "logprob", None)
        if isinstance(v, (int, float)):
            vals.append(float(v))
    if not vals:
        return None
    return round(min(1.0, math.exp(sum(vals) / len(vals))), 3)


HINT_MAX_CHARS = 200
_HINT_OK = re.compile(r"[^A-Za-z0-9'&.\- ]+")


# Short guesses the recognisers make up from noise (cab rumble, a cough, a door): seen in logs and known for the
# Whisper family. Only dropped for doubtful recordings, never for a clear one.
NOISE_GUESSES = {"thank you", "thanks", "thank you for watching", "thanks for watching", "you", "bye",
                 "so", "uh", "um", "hmm", "mm", "oh", "the", "i"}


def maybe_min_confidence() -> float:
    """A doubtful recording's short transcript below this confidence is treated as noise. Env STT_MAYBE_MIN_CONFIDENCE."""
    try:
        return float(os.environ.get("STT_MAYBE_MIN_CONFIDENCE", "0.5"))
    except ValueError:
        return 0.5


def noise_only(text: str, confidence: Optional[float]) -> bool:
    """For a recording the app flagged as doubtful (sound, no clear speech): is this transcript noise, not words?
    Two words or fewer that are a known noise guess, or that the model was sure of less than the threshold.
    Longer text is speech."""
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if not words:
        return True
    if len(words) > 2:
        return False
    if " ".join(words) in NOISE_GUESSES:
        return True
    # No confidence at all (a model without logprobs): keep it, the known noise lines are already out.
    return confidence is not None and confidence < maybe_min_confidence()


def stt_prompt(hint: Optional[str] = None) -> str:
    """The vocabulary hint for the recognizer: the fixed trucker list plus names from the active route
    (destination, the options on screen, towns), so "Lodi" is not heard as "lady". Capped and sanitised."""
    if not hint:
        return PROMPT
    seen, terms = set(), []
    for raw in re.split(r"[,;|\n]+", hint):
        t = _HINT_OK.sub(" ", raw).strip()
        t = " ".join(t.split())
        key = t.lower()
        if t and key not in seen and key not in PROMPT.lower():
            seen.add(key)
            terms.append(t)
    extra = ", ".join(terms)
    if len(extra) > HINT_MAX_CHARS:
        extra = extra[:HINT_MAX_CHARS].rsplit(",", 1)[0]
    return f"{PROMPT} {extra}." if extra else PROMPT


async def transcribe(data: bytes, filename: str, hint: Optional[str] = None) -> str:
    """Speech -> text (see transcribe_full for the confidence)."""
    return (await transcribe_full(data, filename, hint)).text


# A Russian- or Ukrainian-speaking driver says the places and roads in English ("найди Love's на I-80"): the
# recogniser gets no fixed language (a forced "ru" spells Love's as Лавс) and a prompt in the driver's language
# with the English names in it, which sets both the language and the spelling.
PROMPT_RU = ("Дальнобойщик говорит со штурманом по-русски, названия по-английски: найди Love's на I-80, стоянка для "
             "фур, Pilot, Flying J, rest area, CAT scale.")
PROMPT_UK = ("Далекобійник говорить зі штурманом українською, назви англійською: знайди Love's на I-80, стоянка для "
             "фур, Pilot, Flying J, rest area, CAT scale.")


def stt_language_and_prompt(hint: Optional[str]) -> tuple[Optional[str], str]:
    from services import i18n  # lazy: keep voice importable on its own

    code = i18n.lang()
    base = stt_prompt(hint)
    if code == "ru":
        return None, f"{PROMPT_RU} {base}"
    if code == "uk":
        return None, f"{PROMPT_UK} {base}"
    return "en", base


async def transcribe_full(data: bytes, filename: str, hint: Optional[str] = None) -> Transcript:
    stt = llm.client().audio.transcriptions
    language, prompt = stt_language_and_prompt(hint)
    lang_arg = {"language": language} if language else {}
    try:
        buf = io.BytesIO(data)
        buf.name = filename
        res = await asyncio.wait_for(
            stt.create(file=buf, model=STT_MODEL, response_format="json", **lang_arg,
                       prompt=prompt, temperature=0.0, include=["logprobs"]),
            STT_TIMEOUT_S)
        spend.record_stt(STT_MODEL, res, len(data))
        return Transcript(_clean((getattr(res, "text", "") or "").strip()),
                          confidence_of(getattr(res, "logprobs", None)))
    except asyncio.TimeoutError:
        raise SttTimeout(f"{STT_MODEL} took longer than {STT_TIMEOUT_S:.0f}s")  # no fallback: it would double the wait
    except Exception as e:  # noqa: BLE001 - fall back to Whisper with its confidence guard
        logger.warning("stt %s failed, falling back to whisper-1: %s", STT_MODEL, e)
    buf = io.BytesIO(data)
    buf.name = filename
    try:
        res = await asyncio.wait_for(
            stt.create(file=buf, model="whisper-1", response_format="verbose_json", **lang_arg, prompt=prompt),
            STT_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise SttTimeout(f"whisper-1 took longer than {STT_TIMEOUT_S:.0f}s")
    spend.record_stt("whisper-1", res, len(data))
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
                logger.info("stt dropped segment %s no_speech=%.2f logprob=%.2f compression=%.2f", said(seg), nsp, lp, cr)
            else:
                kept.append(seg)
        text = " ".join(kept).strip()
        lps = [_get(s, "avg_logprob") for s in segments if isinstance(_get(s, "avg_logprob"), (int, float))]
        conf = round(min(1.0, math.exp(sum(lps) / len(lps))), 3) if lps else None
    else:
        text = (getattr(res, "text", "") or "").strip()
        conf = None
    return Transcript(_clean(text), conf)


def _clean(text: str) -> str:
    if NOISE_ONLY.fullmatch(text):
        if text:
            logger.info("stt dropped noise-only text %s", said(text))
        return ""
    if PROMPT_ECHO.search(text) or HALLUCINATION.search(text):  # prompt echo / Whisper's stock lines on noise
        logger.info("stt dropped hallucination %s", said(text))
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
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"  # same price per minute as tts-1, better voice, takes INSTRUCTIONS


def fallback_model() -> str:
    """OpenAI TTS model (used whenever ElevenLabs is off or fails). OPENAI_TTS_MODEL=tts-1 switches back."""
    return os.environ.get("OPENAI_TTS_MODEL") or DEFAULT_TTS_MODEL


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
    """ElevenLabs voice id when enabled and the key is set, else None (OpenAI is used).
    Off by default: the app speaks with one female OpenAI voice ("sage") everywhere.
    Set ELEVENLABS_ENABLED=1 to switch back to ElevenLabs. Skipped once the day's spend reaches the cap
    (services/spend.py, stage cheap_voice)."""
    if os.environ.get("ELEVENLABS_ENABLED") != "1" or not os.environ.get("ELEVENLABS_API_KEY"):
        return None
    if spend.voice_cheap():
        return None
    return os.environ.get("ELEVENLABS_VOICE_ID") or ELEVEN_DEFAULT_VOICE


def provider() -> str:
    return "gemini" if gemini_voice() else "elevenlabs" if eleven_voice() else "openai"


def cache_tag(voice: Optional[str] = None, model: Optional[str] = None, eleven_voice_id: Optional[str] = None) -> str:
    """Identifies who speaks a cached line: provider + voice (changes invalidate /speak cache)."""
    if not voice and not model and not eleven_voice_id and gemini_voice():
        # The style prompt and the gain are in the tag too: a new style or level re-records the fixed lines.
        return f"gemini|{gemini_model()}|{gemini_voice()}|g{GEMINI_GAIN_DB}|s{GEMINI_STYLE_TAG}"
    if not voice and not model and eleven_voice():
        return f"elevenlabs|{ELEVEN_MODEL}|{eleven_voice_id or eleven_voice()}"
    # The gain is part of the tag: clips boosted differently must not be served from the old cache.
    return f"openai|{model or fallback_model()}|{voice or DEFAULT_VOICE}|g{OPENAI_GAIN_DB}"


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
    spend.record_tts("elevenlabs", len(text))
    return r.content


# gpt-4o-mini-tts takes a speaking-style instruction (tts-1 ignores it).
INSTRUCTIONS = "Calm, warm, confident co-pilot talking to a truck driver on the road. Natural pace, clear and friendly."
# Russian / Ukrainian lines (services.i18n): the same voice in the driver's language, the English place and road
# names inside them (Love's, Pilot, I-80) said the American way, as drivers say them.
INSTRUCTIONS_RU = ("Спокойный, тёплый, уверенный штурман говорит с дальнобойщиком в дороге, по-русски, естественно и "
                   "чётко. Английские названия мест и трасс (Love's, Pilot, I-80) произноси по-американски.")
INSTRUCTIONS_UK = ("Спокійний, теплий, впевнений штурман говорить з далекобійником у дорозі, українською, природно і "
                   "чітко. Англійські назви місць і трас (Love's, Pilot, I-80) вимовляй по-американськи.")


def lang_of(text: str) -> str:
    """The language of a line by its script: "uk" for Ukrainian letters, "ru" for other Cyrillic, else "en"."""
    if re.search(r"[ІіЇїЄєҐґ]", text or ""):
        return "uk"
    if re.search(r"[А-Яа-яЁё]", text or ""):
        return "ru"
    return "en"


def instructions_for(text: str) -> str:
    """The TTS style for this line, by its script: Ukrainian letters, other Cyrillic, else English."""
    return {"uk": INSTRUCTIONS_UK, "ru": INSTRUCTIONS_RU}.get(lang_of(text), INSTRUCTIONS)


# OpenAI TTS is quiet and uneven: gpt-4o-mini-tts/sage measures -31 to -35 LUFS raw depending on the sentence
# (tts-1 was ~-26), ElevenLabs ~-15, so on a phone speaker in a cab it is barely audible. The spec
# (tests/test_iter25/26) is >= -19 LUFS integrated. +13 dB measured -21.2 / -20.7 / -19.9 on three clips;
# +15 dB measures -16 to -21 (2026-10-29), true peak -1.0 to -5.6 dBTP, so the limiter is not what holds
# the quiet ones back; a fixed gain cannot pin every sentence above -19, that would take loudnorm.
OPENAI_GAIN_DB = 15
_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


async def louder(mp3: bytes) -> bytes:
    """Boosts an OpenAI TTS mp3; returns the original if ffmpeg fails."""
    try:
        p = await asyncio.create_subprocess_exec(
            _FFMPEG, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
            "-af", f"volume={OPENAI_GAIN_DB}dB,alimiter=limit=0.84:level=false",
            "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(p.communicate(mp3), timeout=5)
        except asyncio.TimeoutError:
            p.kill()
            raise
        if p.returncode == 0 and out:
            return out
        logger.warning("tts boost failed: %s", err[:200])
    except Exception as e:  # noqa: BLE001
        logger.warning("tts boost failed: %s", e)
    return mp3


# ---- Gemini TTS (GEMINI_TTS_ENABLED=1) ---------------------------------------------------------------------------
# The voice the owner picked by ear (2026-11-10): gemini-3.8-flash-lite-tts, "Achird", told to sound like a friend in
# the passenger seat. A whole clip takes 1.2 s for "Alright, got one.", 1.9-2.6 s for a sentence or two (live,
# 2026-11-10); streamed (TTS_STREAM=1, synthesize_stream, SSE) the first audio comes in about 0.5 s.
# Rate limits on the TTS models are low: a 429 pauses Gemini like any failure and OpenAI speaks.

GEMINI_TTS_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TTS_DEFAULT_MODEL = "gemini-3.8-flash-lite-tts"
GEMINI_TTS_DEFAULT_VOICE = "Achird"
GEMINI_TTS_TIMEOUT_S = 6.0
GEMINI_TTS_COOLDOWN_S = 60  # after a 429, a timeout or an error, skip Gemini for a while (OpenAI speaks)
GEMINI_PCM_RATE = 24000  # 16-bit mono 24 kHz: a WAV (3.8 models, RIFF header) or raw PCM s16le (older models)
_gemini_down_until = 0.0
_gemini_slots = asyncio.Semaphore(int(os.environ.get("GEMINI_TTS_CONCURRENCY", "2")))

# The style the owner liked, sent as the part's speech_metadata.style. Gemini 3.8 TTS reads the text part as a
# verbatim transcript: the style glued in front of the line ("style\n\ntext", and a "notes / transcript" layout too)
# was read aloud before it, in every sample and in two live calls (2026-11-10, whisper.cpp transcripts). The docs
# (speech-generation, migration guide) put style in speech_metadata for this reason.
GEMINI_STYLE = ("Say this like a relaxed friend riding along in the passenger seat, talking to a trucker buddy: warm, "
                "casual, a little smile, natural pauses, never like an announcer or a GPS.")
GEMINI_STYLE_RU = ("Скажи это как расслабленный друг на пассажирском сиденье, который болтает с приятелем-дальнобойщиком: "
                   "тепло, по-свойски, с лёгкой улыбкой, с живыми паузами, не как диктор и не как навигатор. "
                   "Английские названия говори по-американски.")
GEMINI_STYLE_UK = ("Скажи це як розслаблений друг на пасажирському сидінні, який балакає з приятелем-далекобійником: "
                   "тепло, по-свійськи, з легкою усмішкою, з живими паузами, не як диктор і не як навігатор. "
                   "Англійські назви кажи по-американськи.")
GEMINI_STYLE_TAG = hashlib.sha1("|".join((GEMINI_STYLE, GEMINI_STYLE_RU, GEMINI_STYLE_UK))
                                .encode()).hexdigest()[:8]
# Gemini Lite TTS / Achird measures -20.3 to -22.2 LUFS raw (three live clips, en/ru, 2026-11-10), about 10 dB louder
# than OpenAI sage. Through this chain: +3 dB gave -18.1 to -19.6, +5 dB -16.6 to -18.0, +6 dB -15.9 to -17.3 LUFS
# (peaks -1.4 to -1.6 dBFS, held by the limiter): +6 sits with the boosted OpenAI clips (-16 to -21) and clears
# the >= -19 LUFS spec (see OPENAI_GAIN_DB) with margin.
GEMINI_GAIN_DB = 6


def gemini_model() -> str:
    return os.environ.get("GEMINI_TTS_MODEL") or GEMINI_TTS_DEFAULT_MODEL


def gemini_voice() -> Optional[str]:
    """The Gemini TTS voice when GEMINI_TTS_ENABLED=1 and GEMINI_API_KEY is set, else None. Off by default.
    Skipped once the day's spend reaches the cap (services/spend.py, stage cheap_voice): OpenAI speaks then."""
    if os.environ.get("GEMINI_TTS_ENABLED") != "1" or not os.environ.get("GEMINI_API_KEY"):
        return None
    if spend.voice_cheap():
        return None
    return os.environ.get("GEMINI_TTS_VOICE") or GEMINI_TTS_DEFAULT_VOICE


def gemini_active() -> bool:
    """Gemini would speak the next line now (enabled and not paused after a failure)."""
    return bool(gemini_voice()) and time.monotonic() >= _gemini_down_until


def gemini_style_for(text: str) -> str:
    return {"uk": GEMINI_STYLE_UK, "ru": GEMINI_STYLE_RU}.get(lang_of(text), GEMINI_STYLE)


def gemini_body(text: str, voice_name: str) -> dict:
    """generateContent request: the line as the transcript, the style beside it (never inside it)."""
    return {"contents": [{"parts": [{"text": text, "speech_metadata": {"style": gemini_style_for(text)}}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}}}}


def _gemini_failed() -> None:
    global _gemini_down_until
    _gemini_down_until = time.monotonic() + GEMINI_TTS_COOLDOWN_S


def _gemini_pcm(body: dict) -> Optional[bytes]:
    """candidates[0].content.parts[*].inlineData.data (base64 WAV or raw PCM) -> bytes, or None."""
    try:
        for part in body["candidates"][0]["content"]["parts"]:
            data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
            if data:
                return base64.b64decode(data)
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        return None
    return None


def _is_wav(audio: bytes) -> bool:
    return audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"


def audio_seconds(audio: bytes) -> float:
    """Length of a Gemini clip (WAV or raw PCM, 16-bit mono 24 kHz)."""
    return max(0, len(audio) - (44 if _is_wav(audio) else 0)) / (2 * GEMINI_PCM_RATE)


async def pcm_to_mp3(pcm: bytes, gain_db: float = GEMINI_GAIN_DB) -> Optional[bytes]:
    """Gemini's audio (WAV, or raw PCM s16le 24 kHz mono) -> a boosted mp3 like the other clips, or None."""
    fmt = [] if _is_wav(pcm) else ["-f", "s16le", "-ar", str(GEMINI_PCM_RATE), "-ac", "1"]
    try:
        p = await asyncio.create_subprocess_exec(
            _FFMPEG, "-hide_banner", "-loglevel", "error", *fmt, "-i", "pipe:0",
            "-af", f"volume={gain_db}dB,alimiter=limit=0.84:level=false",
            "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(p.communicate(pcm), timeout=5)
        except asyncio.TimeoutError:
            p.kill()
            raise
        if p.returncode == 0 and out:
            return out
        logger.warning("gemini pcm->mp3 failed: %s", err[:200])
    except Exception as e:  # noqa: BLE001
        logger.warning("gemini pcm->mp3 failed: %s", e)
    return None


async def _gemini(text: str, voice_name: str) -> Optional[bytes]:
    """One clip from Gemini TTS as mp3, or None (paused, 429, timeout, error, no audio: Gemini pauses)."""
    if time.monotonic() < _gemini_down_until:
        return None
    model = gemini_model()
    body = gemini_body(text, voice_name)
    try:
        async with _gemini_slots:
            # extensions: the shared client's spend hook counts this as gemini_tts, not against the Maps grounding
            # call cap (services/spend.py)
            r = await client().post(GEMINI_TTS_URL.format(model=model), json=body,
                                    headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                                    timeout=GEMINI_TTS_TIMEOUT_S, extensions={"spend": "gemini_tts"})
    except Exception as e:  # noqa: BLE001 - timeout or transport error
        logger.warning("Gemini TTS failed, OpenAI speaks for %ds: %s", GEMINI_TTS_COOLDOWN_S, type(e).__name__)
        _gemini_failed()
        return None
    if r.status_code != 200:
        logger.warning("Gemini TTS %s, OpenAI speaks for %ds: %s", r.status_code, GEMINI_TTS_COOLDOWN_S, r.text[:200])
        _gemini_failed()
        return None
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    pcm = _gemini_pcm(payload)
    if not pcm:
        logger.warning("Gemini TTS answered without audio, OpenAI speaks for %ds", GEMINI_TTS_COOLDOWN_S)
        _gemini_failed()
        return None
    spend.record_gemini_tts(audio_seconds(pcm), payload.get("usageMetadata"), len(text) + len(gemini_style_for(text)))
    mp3 = await pcm_to_mp3(pcm)
    if mp3 is None:
        _gemini_failed()
    return mp3


async def synthesize(text: str, voice: Optional[str] = None, model: Optional[str] = None,
                     previous_text: Optional[str] = None, next_text: Optional[str] = None,
                     eleven_voice_id: Optional[str] = None, audio_id: Optional[str] = None) -> Optional[str]:
    """Returns an id for GET /api/voice/audio/{id}, or None if TTS failed.
    Gemini TTS when enabled (and not paused), else ElevenLabs when enabled, then OpenAI; an explicit OpenAI
    voice/model skips both. When Gemini or ElevenLabs fails, OpenAI speaks and the clip is marked as a fallback.
    The clip goes to the in-process cache and to TTS_DIR on disk, so any worker (or this one after a restart) can
    serve the id.
    `audio_id` lets a caller hand out the id before the clip exists (synthesize_later)."""
    text = spoken(text)[:4000]  # everything spoken goes through the text-for-speech layer
    previous_text = spoken(previous_text) if previous_text else None
    next_text = spoken(next_text) if next_text else None
    audio = None
    # An explicit OpenAI voice/model or ElevenLabs voice (voice comparison via /speak) is spoken by that provider.
    gemini = gemini_voice() if not voice and not model and not eleven_voice_id else None
    if gemini:
        audio = await _gemini(text, gemini)
    # Gemini failed: OpenAI at once (no second slow provider in the way); the clip is marked as a fallback.
    eleven = eleven_voice() if not voice and not model and not gemini else None
    if eleven:
        audio = await _eleven(text, eleven_voice_id or eleven, previous_text, next_text)
    fell_back = bool(gemini or eleven) and audio is None
    if audio is None:
        model = model or fallback_model()
        try:
            extra = {"instructions": instructions_for(text)} if model == "gpt-4o-mini-tts" else {}
            res = await llm.client().audio.speech.create(input=text, model=model, voice=voice or DEFAULT_VOICE,
                                                         response_format="mp3", **extra)
            audio = res.content
            spend.record_tts(model, len(text))
        except Exception:
            logger.exception("TTS failed")
            return None
        audio = await louder(audio)
    audio_id = audio_id or uuid.uuid4().hex
    _AUDIO_CACHE[audio_id] = audio
    while len(_AUDIO_CACHE) > _CACHE_MAX:
        _AUDIO_CACHE.popitem(last=False)
    await store_clip(audio_id, audio)
    if fell_back:
        _FALLBACK_IDS[audio_id] = None
        while len(_FALLBACK_IDS) > _CACHE_MAX:
            _FALLBACK_IDS.popitem(last=False)
    return audio_id


_PIN_DIR = Path(__file__).resolve().parent.parent / ".tts_cache"  # where pins lived before TTS_DIR; still read
CLIP_TTL_S = 24 * 3600  # a spoken reply is fetched within seconds; a day on disk is plenty
EVICT_EVERY_S = 300
CROSS_WORKER_WAIT_S = 3.0  # a clip another worker is still making: how long the audio route waits for the file
_last_evict = 0.0
_ID_RE = re.compile(r"[0-9a-f]{32}")


def _clips_dir() -> Path:
    return settings().tts_dir


def _pins_dir() -> Path:
    return _clips_dir() / "pins"  # fixed lines (fillers, onboarding): never evicted


def _clip_path(audio_id: str) -> Path:
    return _clips_dir() / f"{audio_id}.mp3"


def _write_clip_sync(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _evict_sync(now: float) -> int:
    """Clips older than CLIP_TTL_S go (pins live in their own folder and stay)."""
    gone = 0
    d = _clips_dir()
    if not d.exists():
        return 0
    for f in d.glob("*.mp3"):
        try:
            if now - f.stat().st_mtime > CLIP_TTL_S:
                f.unlink()
                gone += 1
        except OSError:
            continue
    return gone


async def store_clip(audio_id: str, data: bytes) -> None:
    """The clip on disk for every worker; a failed write only costs the cross-worker path, never the reply."""
    global _last_evict
    try:
        await asyncio.to_thread(_write_clip_sync, _clip_path(audio_id), data)
    except OSError as e:
        logger.warning("could not store audio clip %s: %s", audio_id, e)
        return
    now = time.monotonic()
    if now - _last_evict > EVICT_EVERY_S:
        _last_evict = now
        try:
            gone = await asyncio.to_thread(_evict_sync, time.time())
            if gone:
                logger.info("tts clips evicted: %d", gone)
        except OSError:
            pass


def _read_clip_sync(audio_id: str) -> Optional[bytes]:
    for f in (_clip_path(audio_id), _pins_dir() / f"{audio_id}.mp3", _PIN_DIR / f"{audio_id}.mp3"):
        if f.exists():
            try:
                return f.read_bytes()
            except OSError:
                return None
    return None


def pin(audio_id: str, key: str) -> str:
    """Stores a fixed line (fillers, onboarding) on disk under a stable id derived from its cache key."""
    data = _AUDIO_CACHE.get(audio_id)
    if data is None or audio_id in _FALLBACK_IDS:
        return audio_id
    stable = _stable_id(key)
    try:
        _write_clip_sync(_pins_dir() / f"{stable}.mp3", data)
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
    """The clip: memory, then disk (this worker's or another's, pins, the old pin folder)."""
    data = _AUDIO_CACHE.get(audio_id)
    if data is None and _ID_RE.fullmatch(audio_id or ""):
        data = _read_clip_sync(audio_id)
        if data is not None:
            _AUDIO_CACHE[audio_id] = data
    return data


_PENDING: dict[str, "asyncio.Task[Optional[str]]"] = {}


def synthesize_later(text: str, previous_text: Optional[str] = None) -> str:
    """Starts TTS in the background and returns the audio id right away (served once ready)."""
    audio_id = uuid.uuid4().hex

    async def run() -> Optional[str]:
        return await synthesize(text, previous_text=previous_text, audio_id=audio_id)  # straight under the handed-out id

    task = asyncio.create_task(run())
    _PENDING[audio_id] = task
    task.add_done_callback(lambda _t: _PENDING.pop(audio_id, None))
    return audio_id


async def wait_audio(audio_id: str) -> Optional[bytes]:
    """The clip once it exists: awaits a synthesis running in this worker; for an id another worker handed out
    and is still making, polls the disk for CROSS_WORKER_WAIT_S."""
    task = _PENDING.get(audio_id)
    if task is not None:
        await asyncio.shield(task)
        return get_audio(audio_id)
    data = get_audio(audio_id)
    if data is not None or not _ID_RE.fullmatch(audio_id or ""):
        return data
    deadline = time.monotonic() + CROSS_WORKER_WAIT_S
    while time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        data = get_audio(audio_id)
        if data is not None:
            return data
    return None


# ---- streaming TTS (TTS_STREAM=1): the driver hears the first chunk while the rest is still being made ----

STREAM_CHUNK = 4096
STREAM_FIRST_CHUNK_S = 6.0  # no audio at all after this -> fall back to the one-shot path
STREAM_MODEL = os.environ.get("OPENAI_TTS_STREAM_MODEL", "gpt-4o-mini-tts")  # tts-1 cannot stream


class StreamBuf:
    """Chunks of one clip as they arrive; readers wait on the condition for more."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.done = False
        self.error: Optional[str] = None
        self.cond = asyncio.Condition()

    @property
    def size(self) -> int:
        return sum(len(c) for c in self.chunks)


_STREAMS: dict[str, StreamBuf] = {}


def is_streaming(audio_id: str) -> bool:
    return audio_id in _STREAMS


MP3_IN = ("-f", "mp3")  # OpenAI streams mp3
# Gemini streams raw PCM. Without nobuffer / a tiny probe ffmpeg sat on the first second of input before it wrote
# any mp3 (first PCM in -> first mp3 out: 1.03 s; with them 0.03 s, measured 2026-11-10). Raw PCM has nothing to
# probe: the format is given.
PCM_IN = ("-fflags", "nobuffer", "-probesize", "32", "-analyzeduration", "0",
          "-f", "s16le", "-ar", str(GEMINI_PCM_RATE), "-ac", "1")


async def _gain_pipe(src, out: "asyncio.Queue", input_fmt: tuple = MP3_IN, gain_db: float = OPENAI_GAIN_DB) -> None:
    """Feed audio chunks (`input_fmt`: mp3 from OpenAI, PCM from Gemini) through one long-lived ffmpeg (+gain_db
    and the limiter, the same loudness as the whole clips) and put the boosted mp3 chunks on `out`. The end is None;
    an error in `src` goes on the queue first (so a stream cut midway is not taken for a finished one). Without
    ffmpeg mp3 passes through untouched; PCM cannot (the app plays mp3), so that raises."""
    try:
        p = await asyncio.create_subprocess_exec(
            _FFMPEG, "-hide_banner", "-loglevel", "error", *input_fmt, "-i", "pipe:0",
            "-af", f"volume={gain_db}dB,alimiter=limit=0.84:level=false",
            "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "-flush_packets", "1", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001 - no ffmpeg: unboosted stream is better than none
        if input_fmt != MP3_IN:
            raise
        logger.warning("stream gain unavailable, passing audio through: %s", e)
        try:
            async for chunk in src:
                await out.put(chunk)
        except Exception as err:  # noqa: BLE001
            await out.put(err)
        await out.put(None)
        return

    async def feed():
        try:
            async for chunk in src:
                p.stdin.write(chunk)
                await p.stdin.drain()
        finally:
            p.stdin.close()

    feeder = asyncio.create_task(feed())
    try:
        while True:
            chunk = await p.stdout.read(STREAM_CHUNK)
            if not chunk:
                break
            await out.put(chunk)
    except BaseException:  # cancelled (first-chunk budget): stop the source and ffmpeg, don't wait on them
        feeder.cancel()
        try:
            p.kill()
        except ProcessLookupError:
            pass
        await asyncio.gather(feeder, return_exceptions=True)
        raise
    fed = (await asyncio.gather(feeder, return_exceptions=True))[0]
    await p.wait()
    if isinstance(fed, Exception):
        await out.put(fed)
    await out.put(None)


async def _openai_stream(text: str):
    """Raw mp3 chunks from gpt-4o-mini-tts as they are generated."""
    model = STREAM_MODEL
    extra = {"instructions": instructions_for(text)} if model == "gpt-4o-mini-tts" else {}
    spend.record_tts(model, len(text))
    async with llm.client().audio.speech.with_streaming_response.create(
            input=text, model=model, voice=DEFAULT_VOICE, response_format="mp3", stream_format="audio",
            **extra) as resp:
        async for chunk in resp.iter_bytes(STREAM_CHUNK):
            if chunk:
                yield chunk


GEMINI_STREAM_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"


class GeminiStreamError(Exception):
    """Gemini's stream answered with an error status (a 429 included)."""


def _sse_events(lines: list[str]):
    """One SSE event's `data:` lines -> its JSON (None for an empty or unparsable event)."""
    data = "\n".join(line[5:].lstrip() for line in lines if line.startswith("data:"))
    if not data:
        return None
    try:
        return json.loads(data)
    except ValueError:
        return None


async def _gemini_stream(text: str, voice_name: str):
    """Raw PCM chunks (s16le, 24 kHz, mono) from Gemini TTS over SSE (streamGenerateContent?alt=sse, the same body
    as generateContent). The first audio comes in about 0.5 s. The cost is recorded at the end, also for a stream
    cut midway: usageMetadata (in the last event) when it came, else the PCM length."""
    model = gemini_model()
    pcm_bytes, usage, header_checked = 0, None, False
    try:
        async with client().stream(
                "POST", GEMINI_STREAM_URL.format(model=model), params={"alt": "sse"}, json=gemini_body(text, voice_name),
                headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, timeout=GEMINI_TTS_TIMEOUT_S,
                extensions={"spend": "gemini_tts"}) as r:
            if r.status_code != 200:
                detail = (await r.aread())[:200]
                raise GeminiStreamError(f"HTTP {r.status_code}: {detail!r}")
            event: list[str] = []
            async for line in r.aiter_lines():
                if line.strip():
                    event.append(line)
                    continue
                body, event = _sse_events(event), []
                if not isinstance(body, dict):
                    continue
                if isinstance(body.get("usageMetadata"), dict):
                    usage = body["usageMetadata"]
                for chunk in _gemini_chunks(body):
                    if not header_checked:
                        header_checked = True
                        if _is_wav(chunk):  # a model that streams WAV: drop the header, the rest is the same PCM
                            chunk = chunk[44:]
                    pcm_bytes += len(chunk)
                    if chunk:
                        yield chunk
            body = _sse_events(event)  # a last event without the blank line after it
            if isinstance(body, dict):
                if isinstance(body.get("usageMetadata"), dict):
                    usage = body["usageMetadata"]
                for chunk in _gemini_chunks(body):
                    pcm_bytes += len(chunk)
                    yield chunk
    finally:
        if pcm_bytes:
            spend.record_gemini_tts(pcm_bytes / (2 * GEMINI_PCM_RATE), usage,
                                    len(text) + len(gemini_style_for(text)))


def _gemini_chunks(body: dict) -> list[bytes]:
    out = []
    try:
        for cand in body.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
                if data:
                    out.append(base64.b64decode(data))
    except (AttributeError, TypeError, ValueError):
        return out
    return out


async def _safe_pump(src, out: asyncio.Queue, input_fmt: tuple = MP3_IN, gain_db: float = OPENAI_GAIN_DB) -> None:
    """_gain_pipe that always ends the queue: an exception becomes an item, then None."""
    try:
        await _gain_pipe(src, out, input_fmt, gain_db)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        await out.put(e)
        await out.put(None)


def _stream_sources(text: str) -> list[tuple[str, object, tuple, float]]:
    """(name, chunk source, input format, gain) to try in order: Gemini first when it is on and not paused, then
    OpenAI. The sources are async generators, made lazily so an unused one never sends a request."""
    sources = []
    gemini = gemini_voice() if gemini_active() else None
    if gemini:
        sources.append(("gemini", lambda: _gemini_stream(text, gemini), PCM_IN, GEMINI_GAIN_DB))
    sources.append(("openai", lambda: _openai_stream(text), MP3_IN, OPENAI_GAIN_DB))
    return sources


async def _stream_one(make_src, input_fmt: tuple, gain_db: float, buf: StreamBuf) -> bool:
    """Streams one source into `buf`. True when it finished; False when it gave no audio (error, nothing within
    STREAM_FIRST_CHUNK_S): the caller tries the next source. Raises when it broke after audio was sent."""
    out: asyncio.Queue = asyncio.Queue()
    pump = asyncio.create_task(_safe_pump(make_src(), out, input_fmt, gain_db))
    got_any = False
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(out.get(), None if got_any else STREAM_FIRST_CHUNK_S)
            except asyncio.TimeoutError:
                raise RuntimeError("no audio within the first-chunk budget")
            if chunk is None:
                break
            if isinstance(chunk, Exception):
                raise chunk
            got_any = True
            async with buf.cond:
                buf.chunks.append(chunk)
                buf.cond.notify_all()
        if not got_any:
            raise RuntimeError("empty stream")
        return True
    except Exception as e:  # noqa: BLE001
        if got_any:
            raise
        logger.warning("streaming TTS gave no audio (%s: %s)", type(e).__name__, str(e)[:200])
        return False
    finally:
        if not pump.done():
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)


async def _run_stream(audio_id: str, text: str, buf: StreamBuf, previous_text: Optional[str]) -> None:
    try:
        for name, make_src, input_fmt, gain_db in _stream_sources(text):
            try:
                if await _stream_one(make_src, input_fmt, gain_db, buf):
                    return
            except Exception as e:  # noqa: BLE001 - cut midway: the driver already hears this voice, no restart
                logger.warning("streaming TTS (%s) cut off: %s", name, e)
                if name == "gemini":
                    _gemini_failed()
                buf.error = "stream cut off"
                return
            if name == "gemini":  # 429, error or too slow before the first audio: pause it, OpenAI speaks
                logger.warning("Gemini TTS stream failed, OpenAI speaks for %ds", GEMINI_TTS_COOLDOWN_S)
                _gemini_failed()
        # no stream gave audio: the one-shot path, same id, same URL
        logger.warning("streaming TTS failed, falling back to one-shot")
        tmp = await synthesize(text, previous_text=previous_text)
        data = _AUDIO_CACHE.pop(tmp, None) if tmp else None
        if data:
            async with buf.cond:
                buf.chunks = [data]
                buf.cond.notify_all()
        else:
            buf.error = "tts failed"
    finally:
        async with buf.cond:
            buf.done = True
            buf.cond.notify_all()
        data = b"".join(buf.chunks)
        if data:
            _AUDIO_CACHE[audio_id] = data
            while len(_AUDIO_CACHE) > _CACHE_MAX:
                _AUDIO_CACHE.popitem(last=False)
        _STREAMS.pop(audio_id, None)


def synthesize_stream(text: str, previous_text: Optional[str] = None) -> str:
    """Starts a streamed clip and returns its id at once; GET /api/voice/audio/{id} serves bytes as they come.
    Gemini when it is on and not paused (first audio in ~0.5 s), else OpenAI; see _run_stream for the fallbacks."""
    audio_id = uuid.uuid4().hex
    buf = StreamBuf()
    _STREAMS[audio_id] = buf
    task = asyncio.create_task(_run_stream(audio_id, spoken(text)[:4000], buf, previous_text))
    _PENDING[audio_id] = task  # wait_audio() on a finished stream behaves like any pending clip
    task.add_done_callback(lambda _t: _PENDING.pop(audio_id, None))
    return audio_id


async def first_chunk(audio_id: str, timeout: float = STREAM_FIRST_CHUNK_S + 2) -> Optional[bytes]:
    """The first bytes of a streaming clip (for a Range probe), or None when it never produced any."""
    buf = _STREAMS.get(audio_id)
    if buf is None:
        data = await wait_audio(audio_id)
        return data[:1] if data else None
    try:
        async with buf.cond:
            await asyncio.wait_for(buf.cond.wait_for(lambda: buf.chunks or buf.done), timeout)
    except asyncio.TimeoutError:
        return None
    return buf.chunks[0][:1] if buf.chunks else None


async def stream_chunks(audio_id: str):
    """Async iterator over a streaming clip's bytes, from the start, until it is done."""
    buf = _STREAMS.get(audio_id)
    if buf is None:
        data = await wait_audio(audio_id)
        if data:
            yield data
        return
    i = 0
    while True:
        async with buf.cond:
            await buf.cond.wait_for(lambda: len(buf.chunks) > i or buf.done)
            pending = buf.chunks[i:]
            done = buf.done
        for c in pending:
            yield c
        i += len(pending)
        if done and i >= len(buf.chunks):
            return
