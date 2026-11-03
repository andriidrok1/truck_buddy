"""Duplex voice for the copilot (spike): a LiveKit agent that hears the driver while it talks.

One LiveKit room per dialog. The app publishes its microphone and sets a `tb.context` attribute with the
same fields as AgentRequest (position, truck, route) minus the text. This worker runs

    Silero VAD -> streaming STT -> end-of-turn model -> our agent (services.agent) -> TTS -> the room

and sends the whole AgentResponse (route, place, options, actions) on the `tb.agent` text topic, so the app
applies it with the same code as a /api/agent reply. Barge-in and false-interruption handling are LiveKit's,
tuned in turn_handling() below.

Run (from backend/):  python -m voice_agent.worker dev
Needs LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET and the backend's own .env (OPENAI_API_KEY, HERE, Google).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import AsyncIterable, Optional

from dotenv import load_dotenv

load_dotenv()

from livekit import agents, rtc  # noqa: E402
from livekit.agents import Agent, AgentSession, JobContext, llm  # noqa: E402
from livekit.plugins import openai as lk_openai, silero  # noqa: E402
from livekit.plugins.turn_detector.english import EnglishModel  # noqa: E402

from models import AgentRequest  # noqa: E402
from services import voice  # noqa: E402
from services.agent import get_agent_provider  # noqa: E402
from voice_agent import policy  # noqa: E402

logger = logging.getLogger("truckbuddy.voice_agent")

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
            "min_words": policy.MIN_INTERRUPT_WORDS,
            "resume_false_interruption": True,
            "false_interruption_timeout": policy.FALSE_INTERRUPT_S,
        },
        "preemptive_generation": False,  # our agent calls HERE and Google; a thrown-away turn costs real money
    }


class Copilot(Agent):
    """Hands each finished driver turn to the existing agent; speaks its reply, ships its effects to the app."""

    def __init__(self, room: rtc.Room, driver: Optional[str], dialog_id: str) -> None:
        super().__init__(instructions="(replies come from services.agent; this prompt is unused)")
        self._room = room
        self._driver = driver
        self._dialog_id = dialog_id

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
        t0 = time.monotonic()
        req = AgentRequest(**{"lat": 0.0, "lng": 0.0, **ctx, "text": text,
                              "session_id": f"{self._driver}:{self._dialog_id}"[:64] if self._driver else self._dialog_id})
        res = await get_agent_provider().handle(req, self._driver)
        ms = int((time.monotonic() - t0) * 1000)
        await self._room.local_participant.send_text(res.model_dump_json(exclude_none=True), topic=AGENT_TOPIC)
        await self._room.local_participant.send_text(json.dumps({"kind": "agent", "ms": ms, "status": res.status}),
                                                     topic=EVENTS_TOPIC)
        if res.reply:
            yield res.reply


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
    )

    async def _event(kind: str, **data) -> None:
        await ctx.room.local_participant.send_text(json.dumps({"kind": kind, "t": time.time(), **data}),
                                                   topic=EVENTS_TOPIC)

    @session.on("agent_false_interruption")
    def _false(ev) -> None:
        asyncio.ensure_future(_event("false_interruption"))

    @session.on("user_input_transcribed")
    def _heard(ev) -> None:
        logger.info("heard final=%s %r", ev.is_final, ev.transcript)

    @session.on("metrics_collected")
    def _metrics(ev) -> None:
        logger.info("metrics %s", ev.metrics)

    await session.start(agent=Copilot(ctx.room, driver, dialog_id), room=ctx.room)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint, agent_name="truckbuddy-copilot"))
