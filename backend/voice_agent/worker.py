"""Duplex voice for the copilot (spike): a LiveKit agent that hears the driver while it talks.

One LiveKit room per dialog. The app publishes its microphone and sets a `tb.context` attribute with the
same fields as AgentRequest (position, truck, route) minus the text. This worker runs

    Silero VAD -> streaming STT -> end-of-turn model -> our agent (services.agent) -> TTS -> the room

and sends the whole AgentResponse (route, place, options, actions) on the `tb.agent` text topic, so the app
applies it with the same code as a /api/agent reply. Barge-in runs on LiveKit's machinery (ducking, resume after
a false interruption) with our rule in the gate (voice_agent.gate, voice_agent.policy). A reply the driver cut
off stays in the dialog history only as far as it was heard. Each turn writes one `turn {json}` log line in the
server's format plus the voice fields (eou_ms, first_audio_ms, interrupted, heard_chars), so
scripts/turn_stats.py reads it too.

Run (from backend/):  python -m voice_agent.worker dev
Needs LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET and the backend's own .env (OPENAI_API_KEY, HERE, Google).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import AsyncIterable, Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()

from livekit import agents, rtc  # noqa: E402
from livekit.agents import Agent, AgentSession, JobContext, llm, metrics  # noqa: E402
from livekit.plugins import openai as lk_openai, silero  # noqa: E402
from livekit.plugins.turn_detector.english import EnglishModel  # noqa: E402

from models import AgentRequest  # noqa: E402
from services import timing, voice  # noqa: E402
from services.agent import get_agent_provider  # noqa: E402
from services.logs import said  # noqa: E402
from voice_agent import gate, policy  # noqa: E402

logger = logging.getLogger("truckbuddy.voice_agent")

TTS_GAIN = 10 ** (float(os.environ.get("VOICE_TTS_GAIN_DB", "9")) / 20)
NO_POSITION = "I can't see where you are yet. Give me a second and ask again."
AGENT_FAILED = "Sorry, something went wrong on my end. Say that again?"

CONTEXT_ATTR = "tb.context"
AGENT_TOPIC = "tb.agent"
EVENTS_TOPIC = "tb.voice"


def turn_handling() -> dict:
    """Barge-in and end of turn. Numbers are the plan's starting points (2026-11-03), tuned on the audio rig."""
    return {
        "turn_detection": EnglishModel(),
        "endpointing": {"min_delay": policy.MIN_ENDPOINT_S, "max_delay": policy.MAX_ENDPOINT_S},
        "interruption": {
            "mode": "vad",
            "min_duration": policy.MIN_INTERRUPT_S,
            # VOICE_MIN_WORDS=0: stop on voice alone (LiveKit pauses, then resumes if no words come); rig experiments
            "min_words": int(os.environ.get("VOICE_MIN_WORDS", policy.MIN_INTERRUPT_WORDS)),
            "resume_false_interruption": True,
            "false_interruption_timeout": policy.FALSE_INTERRUPT_S,
        },
        "preemptive_generation": {"enabled": False},  # our agent calls HERE and Google; a thrown-away turn costs real money
    }


def louder(frame: rtc.AudioFrame) -> rtc.AudioFrame:
    x = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768
    y = np.tanh(x * TTS_GAIN) if TTS_GAIN > 1 else x  # soft limit instead of clipping
    pcm = (y * 32767).astype(np.int16)
    return rtc.AudioFrame(pcm.tobytes(), frame.sample_rate, frame.num_channels, frame.samples_per_channel)


class Copilot(Agent):
    """Hands each finished driver turn to the existing agent; speaks its reply, ships its effects to the app."""

    def __init__(self, room: rtc.Room, driver: Optional[str], dialog_id: str) -> None:
        super().__init__(instructions="(replies come from services.agent; this prompt is unused)")
        self._room = room
        self._driver = driver
        self._dialog_id = dialog_id
        self.sid = (f"{driver}:{dialog_id}" if driver else dialog_id)[:64]
        self.reply_now = ""  # what the agent is saying, for the after-a-question barge-in rule
        self.turn: dict = {}  # this turn's log fields, written out when the reply ends (voice_agent.worker.entrypoint)

    def _context(self) -> dict:
        for p in self._room.remote_participants.values():
            raw = p.attributes.get(CONTEXT_ATTR)
            if raw:
                try:
                    return json.loads(raw)
                except ValueError:
                    logger.warning("bad %s from %s", CONTEXT_ATTR, p.identity)
        return {}

    async def llm_node(self, chat_ctx: llm.ChatContext, tools, model_settings) -> AsyncIterable[str]:
        text = ""
        for item in reversed(chat_ctx.items):
            if getattr(item, "role", None) == "user":
                text = item.text_content or ""
                break
        if not text.strip():
            return
        ctx = self._context()
        if "lat" not in ctx or "lng" not in ctx:
            logger.warning("no %s from the app yet; not running the agent at (0, 0)", CONTEXT_ATTR)
            yield NO_POSITION
            return
        t0 = time.monotonic()
        timing.begin()
        try:
            req = AgentRequest(**{**ctx, "text": text, "session_id": self.sid})
            task = asyncio.ensure_future(get_agent_provider().handle(req, self._driver))
            done, _ = await asyncio.wait({task}, timeout=policy.FILLER_AFTER_S)
            if not done:  # HERE and Google take seconds: say we heard, then the answer follows in the same breath
                filler = random.choice(policy.FILLERS)
                self.turn["filler"] = True
                self.reply_now = filler
                yield filler + " "
            res = await task
        except Exception:  # noqa: BLE001 - a failed turn is said out loud, never silence
            logger.exception("agent turn failed")
            self.turn.update(status="error", reply_chars=len(AGENT_FAILED))
            self.reply_now = AGENT_FAILED
            yield AGENT_FAILED
            return
        ms = int((time.monotonic() - t0) * 1000)
        notes = timing.notes()
        self.turn.update({"status": res.status, "intent": notes.get("intent") or res.intent or "-", "agent_ms": ms,
                          **{k: v for k, v in notes.items() if k != "intent"}, **timing.current(),
                          "reply_chars": len(res.reply or "")})
        await self._room.local_participant.send_text(res.model_dump_json(exclude_none=True), topic=AGENT_TOPIC)
        await self._room.local_participant.send_text(json.dumps({"kind": "agent", "ms": ms, "status": res.status}),
                                                     topic=EVENTS_TOPIC)
        if res.reply:
            self.reply_now = res.reply
            yield res.reply

    async def tts_node(self, text: AsyncIterable[str], model_settings) -> AsyncIterable[rtc.AudioFrame]:
        """OpenAI TTS comes out around -31 to -35 LUFS (services/voice.py boosts its mp3s for the same reason);
        raise it with a soft limiter so the phone speaker in a cab can carry it."""
        async for frame in Agent.default.tts_node(self, text, model_settings):
            yield louder(frame)


def _driver_of(room_name: str) -> tuple[Optional[str], str]:
    """Room `drv_<id>:<dialog>` -> (driver, dialog). Anything else is a test room without a driver."""
    if room_name.startswith("drv_") and ":" in room_name:
        d, dialog = room_name.split(":", 1)
        return d, dialog
    return None, room_name


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    driver, dialog_id = _driver_of(ctx.room.name)
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=lk_openai.STT(model=os.environ.get("VOICE_STT_MODEL", "gpt-4o-mini-transcribe"), use_realtime=True,
                          noise_reduction_type="far_field", prompt=voice.stt_prompt()),
        llm=lk_openai.LLM(model="gpt-5.4-mini"),  # never called: llm_node above replaces it
        tts=lk_openai.TTS(model="gpt-4o-mini-tts", voice=voice.DEFAULT_VOICE, instructions=voice.INSTRUCTIONS),
        turn_handling=turn_handling(),
        # LiveKit feeds STT silence for the first 3 s of agent speech by default (waiting for a server-side AEC to
        # settle), which drops whatever the driver says over the start of a reply ("No wait, find one closer" was
        # lost on the rig, 2026-11-03). The phone cancels echo in hardware from the first frame; tune on the device.
        aec_warmup_duration=float(os.environ.get("VOICE_AEC_WARMUP_S", "0.5")) or None,
    )

    copilot = Copilot(ctx.room, driver, dialog_id)
    gate.install(lambda: copilot.reply_now)  # one job per process, so the module-level gate sees this session only
    user_done_at: list[float] = [0.0]  # when the driver last stopped talking (wall clock)

    async def _event(kind: str, **data) -> None:
        await ctx.room.local_participant.send_text(json.dumps({"kind": kind, "t": time.time(), **data}),
                                                   topic=EVENTS_TOPIC)

    @session.on("agent_false_interruption")
    def _false(ev) -> None:
        copilot.turn["false_interruptions"] = copilot.turn.get("false_interruptions", 0) + 1
        asyncio.ensure_future(_event("false_interruption"))

    @session.on("user_input_transcribed")
    def _heard(ev) -> None:
        if ev.is_final:
            logger.info("heard %s", said(ev.transcript))

    @session.on("metrics_collected")
    def _metrics(ev) -> None:
        m = ev.metrics
        if isinstance(m, metrics.EOUMetrics):
            copilot.turn["eou_ms"] = int(m.end_of_utterance_delay * 1000)
            copilot.turn["stt"] = int(m.transcription_delay * 1000)
        elif isinstance(m, metrics.TTSMetrics) and "tts_ttfb_ms" not in copilot.turn:
            copilot.turn["tts_ttfb_ms"] = int(m.ttfb * 1000)

    @session.on("user_state_changed")
    def _user(ev) -> None:
        if ev.old_state == "speaking" and ev.new_state != "speaking":
            user_done_at[0] = ev.created_at

    @session.on("agent_state_changed")
    def _agent(ev) -> None:
        if ev.new_state == "speaking" and "first_audio_ms" not in copilot.turn and user_done_at[0]:
            copilot.turn["first_audio_ms"] = int((ev.created_at - user_done_at[0]) * 1000)

    @session.on("conversation_item_added")
    def _item(ev) -> None:
        item = ev.item
        if getattr(item, "role", None) != "assistant":
            return
        heard = item.text_content or ""
        t = copilot.turn
        t.update({"interrupted": bool(item.interrupted), "heard_chars": len(heard)})
        if item.interrupted:
            asyncio.ensure_future(_cut_off(heard))
        log_turn(copilot.sid, driver, t)
        asyncio.ensure_future(_event("turn", **{k: v for k, v in t.items() if k != "req"}))
        copilot.turn = {}
        copilot.reply_now = ""

    async def _cut_off(heard: str) -> None:
        try:
            await get_agent_provider().heard_only(copilot.sid, heard)
        except Exception as e:  # noqa: BLE001 - history stays as it was
            logger.warning("cut-off reply not trimmed for %s: %s", copilot.sid, e)

    await session.start(agent=copilot, room=ctx.room)


def log_turn(sid: str, driver: Optional[str], fields: dict) -> None:
    """One `turn {json}` line per voice turn, the server's format (server._finish) with mode=duplex."""
    out = {"device": driver, "sid": sid, "mode": "duplex", "status": fields.get("status", "-"),
           "intent": fields.get("intent", "-"), **{k: v for k, v in fields.items() if k not in ("status", "intent")}}
    logger.info("turn %s", json.dumps(out, separators=(",", ":"), default=str))


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint, agent_name="truckbuddy-copilot"))
