"""What the paid APIs cost today, and what the service stops doing when the day's money runs out.

Every paid call records an estimate in USD: OpenAI chat from the usage tokens, speech-to-text from the audio
seconds (or tokens, or the upload size when the response has neither), TTS from the characters spoken (Gemini TTS
from its text and audio tokens); Gemini (Maps grounding) and HERE per call. The prices are the tables below, the
one place to edit when a price changes.

Counters are per US Pacific day (like services/quota.py) in the `spend_daily` table (migration 0013), so they
survive a restart and every worker sees the others' spend. A call never waits on the database: it adds to this
worker's pending counters, and a background flush (at most every FLUSH_S) writes them and reads back the day's
totals; the totals any worker uses are the last read plus what it has not written yet. With the store off
(MEMORY_STORE=off) the counters live in this worker's memory only.

DAILY_USD_CAP (default 5, dollars, the whole service) sets the stages, by today's spend against the cap:

    >= 80%   alert         one alert, nothing changes for drivers
    >= 100%  cheap_voice   ElevenLabs and Gemini TTS are skipped (OpenAI TTS only); /speak serves only lines already made and
                           answers 503 for new ones, so the app says them with the phone's own voice. Copilot
                           replies stay spoken: the app has no local voice for them, a silent reply in a moving
                           truck is worse than the cent it costs.
    >= 110%  no_satellite  no new satellite parking checks (cached ones still answer) and no Gemini Maps grounding
                           (the star rating is said instead)
    >= 120%  no_ai         copilot turns get AI_PAUSED (one stored clip, no model call); map, routes and place
                           search keep working

The cap is where degrading starts, so the worst day costs about 1.2x the cap plus what was in flight. Each
threshold sends one alert per day (log line always, Telegram when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are
set), de-duplicated across workers through the same table.

Per-provider call caps, so a loop in the code can't burn a quota in an hour whatever the dollars say:
GEMINI_CALLS_PER_DAY (default 200, Maps grounding only: Gemini TTS is counted apart as gemini_tts, with no call
cap of its own; the cheap_voice stage stops it) and HERE_CALLS_PER_DAY (default 3000). Over the cap the call is refused
before it leaves (ProviderCapped, a transport error the callers already survive: routing falls back to ORS,
grounding to the rating), with one alert per day.

0 turns any cap off. Calls made for a tester token are not counted (like quota.py), and testers are never refused
a turn.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
from typing import Optional

import httpx

from services import db, quota

logger = logging.getLogger("truckbuddy.spend")

# ---- prices (USD; checked 2026-11-09, edit here) ---------------------------------------------------------------

CHAT_PER_M = {  # model: (input, cached input, output) per 1M tokens; reasoning tokens bill as output
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.5": (5.00, 0.50, 30.00),
}
CHAT_UNKNOWN = "gpt-5.5"  # a model missing from the table is priced as the dearest one
STT_PER_MIN = {"gpt-4o-mini-transcribe": 0.003, "gpt-4o-transcribe": 0.006, "whisper-1": 0.006}
STT_TOKENS_PER_M = {"gpt-4o-mini-transcribe": (1.25, 5.00), "gpt-4o-transcribe": (2.50, 10.00)}  # audio in, text out
STT_BYTES_PER_S = 8000  # 64 kbps: seconds from the upload size when the response has no duration (errs high)
TTS_PER_M_CHARS = {"gpt-4o-mini-tts": 17.0, "tts-1": 15.0, "tts-1-hd": 30.0, "elevenlabs": 40.0}
# HERE: routing past the free tier (EUR 2.20/1K; geocoding is cheaper). Gemini: 3.x Maps grounding per prompt,
# the free monthly allowance ignored.
PER_CALL = {"here": 0.0025, "gemini": 0.014}
METERED_HOSTS = (("hereapi.com", "here"), ("generativelanguage.googleapis.com", "gemini"))
# Gemini TTS per 1M tokens: (text in, audio out). Official for gemini Flash TTS; the Lite TTS model has no published
# price (2026-11-10), so it is priced the same as an upper bound. Audio is 25 tokens per second of speech.
GEMINI_TTS_PER_M = (0.50, 10.00)
GEMINI_TTS_TOKENS_PER_S = 25
GEMINI_TTS_CHARS_PER_TOKEN = 4  # text tokens from the prompt length when the response has no usageMetadata

# ---- caps and stages -------------------------------------------------------------------------------------------

CAP_DEFAULT = 5.0
CALLS_DEFAULT = {"gemini": 200, "here": 3000}
CALLS_SHOWN = ("gemini", "here", "gemini_tts")  # call counters in /health (gemini_tts has no cap)
STAGES = ("normal", "alert", "cheap_voice", "no_satellite", "no_ai")
THRESHOLDS = {"alert": 0.8, "cheap_voice": 1.0, "no_satellite": 1.1, "no_ai": 1.2}
AI_PAUSED = ("I'm taking a break from answering for the rest of today; I'm back at midnight Pacific. "
             "The map and the routes keep working.")
FLUSH_S = 2.0  # pending counters reach the store at most this late
REFRESH_S = 10.0  # the other workers' spend is read at least this often while this one is busy
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


class ProviderCapped(httpx.TransportError):
    """The provider's daily call cap is reached; the call was not sent."""


_exempt: contextvars.ContextVar[bool] = contextvars.ContextVar("spend_exempt", default=False)
_pending: dict[tuple[str, str], float] = {}  # (day, key) -> recorded here, not yet in the store
_shared: dict[str, float] = {}  # key -> today's total in the store at the last read
_shared_day = ""
_alerted: set[tuple[str, str]] = set()  # (day, alert key) this worker already handled
_tasks: set[asyncio.Task] = set()
_flush_task: Optional[asyncio.Task] = None
_flush_lock: tuple = (None, None)  # (loop, asyncio.Lock)
_last_read = 0.0


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, str(default))))
    except ValueError:
        return default


def cap_usd() -> float:
    return _float_env("DAILY_USD_CAP", CAP_DEFAULT)


def call_cap(provider: str) -> int:
    return int(_float_env(f"{provider.upper()}_CALLS_PER_DAY", CALLS_DEFAULT[provider]))


def exempt_role(role: Optional[str]) -> None:
    """Called once per request with the token's role: a tester's calls are not counted from here on."""
    _exempt.set(role == "tester")


def is_exempt() -> bool:
    return _exempt.get()


# ---- pricing ---------------------------------------------------------------------------------------------------

def price_chat(model: str, usage: dict) -> float:
    inp, cached_in, out = CHAT_PER_M.get(model) or CHAT_PER_M[CHAT_UNKNOWN]
    prompt, cached = int(usage.get("prompt_tokens") or 0), int(usage.get("cached_tokens") or 0)
    cached = min(cached, prompt)
    return ((prompt - cached) * inp + cached * cached_in + int(usage.get("completion_tokens") or 0) * out) / 1e6


def _get(obj, name, default=None):
    if obj is None:
        return default
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def price_stt(model: str, res=None, nbytes: int = 0) -> float:
    """From the transcription response: usage tokens, usage seconds or `duration` (whisper verbose_json); else
    the upload size at STT_BYTES_PER_S."""
    usage = _get(res, "usage")
    if _get(usage, "type") == "tokens" and model in STT_TOKENS_PER_M:
        audio_in, text_out = STT_TOKENS_PER_M[model]
        return (int(_get(usage, "input_tokens", 0) or 0) * audio_in + int(_get(usage, "output_tokens", 0) or 0) * text_out) / 1e6
    seconds = _get(usage, "seconds") if _get(usage, "type") == "duration" else None
    if seconds is None:
        seconds = _get(res, "duration")
    if not isinstance(seconds, (int, float)):
        seconds = nbytes / STT_BYTES_PER_S
    return float(seconds) / 60 * STT_PER_MIN.get(model, max(STT_PER_MIN.values()))


def price_tts(model: str, chars: int) -> float:
    return chars * TTS_PER_M_CHARS.get(model, max(TTS_PER_M_CHARS.values())) / 1e6


def price_gemini_tts(audio_s: float, usage: Optional[dict] = None, prompt_chars: int = 0) -> float:
    """From usageMetadata (promptTokenCount text in, candidatesTokenCount audio out) when given; else the audio
    seconds at GEMINI_TTS_TOKENS_PER_S and the prompt length at GEMINI_TTS_CHARS_PER_TOKEN."""
    inp, out = GEMINI_TTS_PER_M
    usage = usage if isinstance(usage, dict) else {}
    text_in = usage.get("promptTokenCount")
    audio_out = usage.get("candidatesTokenCount")
    if not isinstance(text_in, (int, float)):
        text_in = prompt_chars / GEMINI_TTS_CHARS_PER_TOKEN
    if not isinstance(audio_out, (int, float)):
        audio_out = audio_s * GEMINI_TTS_TOKENS_PER_S
    return (text_in * inp + audio_out * out) / 1e6


# ---- recording -------------------------------------------------------------------------------------------------

def record(provider: str, usd: float, calls: int = 0) -> None:
    """Adds one call's estimate to today's counters (nothing for a tester); never waits on the store."""
    if _exempt.get() or (usd <= 0 and not calls):
        return
    today = quota.day()
    if db.store() == "off":  # nothing ever writes them out: yesterday's counters just go
        for k in [k for k in _pending if k[0] != today]:
            del _pending[k]
    if usd > 0:
        _pending[(today, f"usd:{provider}")] = _pending.get((today, f"usd:{provider}"), 0.0) + usd
    if calls:
        _pending[(today, f"calls:{provider}")] = _pending.get((today, f"calls:{provider}"), 0.0) + calls
    _check_alerts()
    _kick()


def record_chat(model: str, usage: dict) -> None:
    record("openai", price_chat(model, usage or {}))


def record_stt(model: str, res=None, nbytes: int = 0) -> None:
    record("openai", price_stt(model, res, nbytes))


def record_tts(model: str, chars: int) -> None:
    record("elevenlabs" if model == "elevenlabs" else "openai", price_tts(model, chars))


def record_gemini_tts(audio_s: float, usage: Optional[dict] = None, prompt_chars: int = 0) -> None:
    """The cost of one Gemini TTS clip (its call was already counted by on_request)."""
    record("gemini_tts", price_gemini_tts(audio_s, usage, prompt_chars))


async def on_request(request: httpx.Request) -> None:
    """httpx request hook on the shared client (services/http.py): counts HERE and Gemini calls and refuses one
    over its daily cap before it is sent. A Gemini TTS call (extensions {"spend": "gemini_tts"}, services/voice.py)
    is counted as gemini_tts and never refused here: its cost is recorded from the reply, and the spend stage
    switches it off."""
    if request.extensions.get("spend") == "gemini_tts":
        record("gemini_tts", 0.0, calls=1)
        return
    host = request.url.host or ""
    provider = next((p for suffix, p in METERED_HOSTS if host == suffix or host.endswith("." + suffix)), None)
    if provider is None:
        return
    if not allow_call(provider):
        raise ProviderCapped(f"{provider} daily call cap reached", request=request)
    record(provider, PER_CALL[provider], calls=1)


# ---- totals and stages -----------------------------------------------------------------------------------------

def _value(key: str) -> float:
    today = quota.day()
    shared = _shared.get(key, 0.0) if _shared_day == today else 0.0
    return shared + _pending.get((today, key), 0.0)


def today_usd() -> float:
    today = quota.day()
    keys = {k for (d, k) in _pending if d == today} | (set(_shared) if _shared_day == today else set())
    return sum(_value(k) for k in keys if k.startswith("usd:"))


def calls_today(provider: str) -> int:
    return int(_value(f"calls:{provider}"))


def stage() -> str:
    _kick(refresh_only=True)
    cap = cap_usd()
    if cap <= 0:
        return "normal"
    frac = today_usd() / cap
    reached = [s for s in STAGES[1:] if frac >= THRESHOLDS[s]]
    return reached[-1] if reached else "normal"


def at_least(name: str) -> bool:
    return STAGES.index(stage()) >= STAGES.index(name)


def voice_cheap() -> bool:
    return at_least("cheap_voice")


def satellite_open() -> bool:
    return not at_least("no_satellite")


def turns_open(role: Optional[str] = "driver") -> bool:
    return role == "tester" or not at_least("no_ai")


def allow_call(provider: str) -> bool:
    if _exempt.get():
        return True
    if provider == "gemini" and not satellite_open():
        return False
    cap = call_cap(provider)
    if cap and calls_today(provider) >= cap:
        _alert(f"cap:{provider}", f"TruckBuddy: {provider} hit its daily call cap ({cap}); further calls refused "
                                  "until midnight Pacific.")
        return False
    return True


def health() -> dict:
    return {"spend_today_usd": round(today_usd(), 2), "spend_cap_usd": cap_usd(), "spend_stage": stage(),
            "calls_today": {p: calls_today(p) for p in CALLS_SHOWN}}


# ---- alerts ----------------------------------------------------------------------------------------------------

def _check_alerts() -> None:
    cap = cap_usd()
    if cap <= 0:
        return
    usd = today_usd()
    for name in STAGES[1:]:
        if usd >= THRESHOLDS[name] * cap:
            pct = round(THRESHOLDS[name] * 100)
            what = "" if name == "alert" else f"; now: {name}"
            _alert(f"usd:{pct}", f"TruckBuddy spend today ${usd:.2f}, {pct}% of the ${cap:g} daily cap{what}.")


def _alert(key: str, text: str) -> None:
    """One alert per key per Pacific day: this worker remembers it, the store makes sure only one worker sends."""
    mark = (quota.day(), key)
    if mark in _alerted:
        return
    _alerted.add(mark)
    _spawn(_send_alert(mark[0], key, text))


async def _send_alert(day: str, key: str, text: str) -> bool:
    try:
        if not await _claim(day, f"alert:{key}"):
            return False
    except Exception as e:  # noqa: BLE001 - better a second alert than none
        logger.warning("spend alert claim failed: %s", type(e).__name__)
    logger.warning("spend alert: %s", text)
    await _telegram(text)
    return True


async def _telegram(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return
    from services.http import client  # lazy: http imports this module for its request hook
    try:
        r = await client().post(TELEGRAM_URL.format(token=token), json={"chat_id": chat, "text": text}, timeout=5)
        if r.is_error:
            logger.warning("telegram alert failed: HTTP %s", r.status_code)
    except Exception as e:  # noqa: BLE001 - the URL carries the token: only the error type is logged
        logger.warning("telegram alert failed: %s", type(e).__name__)


# ---- the store -------------------------------------------------------------------------------------------------

def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:  # no loop (a sync caller): nothing to send it with
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _kick(refresh_only: bool = False) -> None:
    """Schedules a flush (write pending + read totals) when one is due and none is running."""
    global _flush_task
    if db.store() == "off" or (_flush_task is not None and not _flush_task.done()):
        return
    if refresh_only and time.monotonic() - _last_read < REFRESH_S:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _flush_task = loop.create_task(_flush_soon())
    _tasks.add(_flush_task)
    _flush_task.add_done_callback(_tasks.discard)


async def _flush_soon() -> None:
    while True:
        wait = FLUSH_S - (time.monotonic() - _last_read)
        if wait > 0:
            await asyncio.sleep(wait)
        await flush()
        if not _pending:
            return


async def _claim(day: str, key: str) -> bool:
    """True for the first worker to claim this key today (a row in spend_daily); always True with no store."""
    k = db.store()
    if k == "off":
        return True
    if k == "sqlite":
        return await db._sq_run(lambda con: con.execute(
            "INSERT OR IGNORE INTO spend_daily (day, key, value) VALUES (?, ?, 1)", (day, key)).rowcount == 1)
    pool = await db._db()
    return await pool.fetchval("INSERT INTO spend_daily (day, key, value) VALUES ($1, $2, 1) "
                               "ON CONFLICT (day, key) DO NOTHING RETURNING key", day, key) is not None


async def _write_and_read(batch: dict[tuple[str, str], float], today: str) -> dict[str, float]:
    rows = [(d, k, v) for (d, k), v in batch.items()]
    if db.store() == "sqlite":
        def _go(con):
            if rows:
                con.execute("BEGIN")
                try:
                    con.executemany("INSERT INTO spend_daily (day, key, value) VALUES (?, ?, ?) ON CONFLICT(day, key) "
                                    "DO UPDATE SET value = spend_daily.value + excluded.value", rows)
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
            return con.execute("SELECT key, value FROM spend_daily WHERE day = ?", (today,)).fetchall()
        got = await db._sq_run(_go)
    else:
        pool = await db._db()
        if rows:
            async with pool.acquire() as con:
                async with con.transaction():
                    await con.executemany("INSERT INTO spend_daily (day, key, value) VALUES ($1, $2, $3) "
                                          "ON CONFLICT (day, key) DO UPDATE SET value = spend_daily.value + EXCLUDED.value",
                                          rows)
        got = [tuple(r) for r in await pool.fetch("SELECT key, value FROM spend_daily WHERE day = $1", today)]
    return {k: float(v) for k, v in got}


async def flush() -> None:
    """Writes this worker's pending counters and reads back today's totals (every worker's). One flush at a time
    per worker: two at once would both write the same pending counters."""
    global _flush_lock
    if db.store() == "off":
        return
    loop = asyncio.get_running_loop()
    if _flush_lock[0] is not loop:  # a lock is bound to the loop it first waited on
        _flush_lock = (loop, asyncio.Lock())
    async with _flush_lock[1]:
        await _flush_locked()


async def _flush_locked() -> None:
    global _shared, _shared_day, _last_read
    batch = dict(_pending)
    today = quota.day()
    try:
        totals = await _write_and_read(batch, today)
    except Exception as e:  # noqa: BLE001 - the counters stay pending and go with the next flush
        logger.warning("spend flush failed: %s", type(e).__name__)
        _last_read = time.monotonic()
        return
    for key, v in batch.items():  # what was written is now in the totals; newer calls stay pending
        left = _pending.get(key, 0.0) - v
        if left > 1e-12:
            _pending[key] = left
        else:
            _pending.pop(key, None)
    _shared, _shared_day, _last_read = totals, today, time.monotonic()
    for k in totals:
        if k.startswith("alert:"):
            _alerted.add((today, k[len("alert:"):]))
    _check_alerts()


async def refresh() -> None:
    """Today's totals from the store (startup: the spend so far survives a restart)."""
    await flush()


async def close() -> None:
    """Shutdown: the last pending counters reach the store."""
    if _pending:
        await flush()


def pending_tasks() -> list:
    return [t for t in _tasks if not t.done()]


def reset() -> None:
    """Forgets this worker's counters (tests; a simulated restart)."""
    global _shared, _shared_day, _flush_task, _last_read
    _pending.clear()
    _alerted.clear()
    _shared, _shared_day, _flush_task, _last_read = {}, "", None, 0.0
