"""Audio rig for duplex voice: a fake driver joins a LiveKit room, plays scripted clips, and times the agent.

    python -m voice_agent.rig --scenario all [--snr 10]

Each scenario creates its own room (so each is a fresh dialog), dispatches truckbuddy-copilot into it, plays
16 kHz clips from rig_clips/ as the driver's microphone (optionally under synthetic cab noise), watches the
agent's audio track, and prints:
  first_audio_ms  end of the driver's clip -> first agent audio (how fast it answers a finished sentence)
  early      the agent spoke during a mid-sentence pause (should be False)
  stop_ms    start of a barge-in clip -> the agent's audio goes quiet (how fast it shuts up)
  interrupted     whether a backchannel ("uh-huh") cut the reply (should be False)
The agent's audio goes to rig_out/<room>.wav for listening. Costs money: STT, the agent (HERE, Google), TTS.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from dotenv import load_dotenv
from livekit import api, rtc

load_dotenv()

HERE = Path(__file__).resolve().parent
CLIPS = HERE / "rig_clips"
OUT = HERE / "rig_out"
SR = 16000
FRAME = SR // 100  # 10 ms
AGENT = "truckbuddy-copilot"
SPEAKING_DB = -45.0  # agent frame louder than this = the agent is talking
CONTEXT = {"lat": 38.5816, "lng": -121.4944}  # downtown Sacramento


def clip(name: str) -> np.ndarray:
    with wave.open(str(CLIPS / f"{name}.wav")) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def silence(s: float) -> np.ndarray:
    return np.zeros(int(s * SR), dtype=np.float32)


def cab_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """Brown noise plus an engine hum: a rough stand-in for a cab at highway speed."""
    brown = np.cumsum(rng.standard_normal(n)).astype(np.float32)
    brown -= np.convolve(brown, np.ones(400) / 400, mode="same")  # drop the DC drift
    t = np.arange(n) / SR
    hum = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((38, 76, 114, 152), start=1)).astype(np.float32)
    x = brown / (np.abs(brown).max() + 1e-9) + 0.5 * hum / np.abs(hum).max()
    return x / np.sqrt(np.mean(x ** 2))


def db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)


class Driver:
    """The fake driver's side of one room."""

    def __init__(self, room_name: str, snr: Optional[float]) -> None:
        self.room_name = room_name
        self.room = rtc.Room()
        self.source = rtc.AudioSource(SR, 1)
        self.snr = snr
        self.rng = np.random.default_rng(7)
        self.agent_frames: list[np.ndarray] = []
        self.speaking_since: Optional[float] = None
        self.quiet_since: Optional[float] = time.monotonic()
        self.talk_log: list[tuple[float, bool]] = []  # (time, speaking) transitions
        self.events: list[dict] = []
        self.replies: list[str] = []
        self._mic: Optional[asyncio.Task] = None
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue()

    async def join(self) -> None:
        token = (api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
                 .with_identity("rig-driver").with_name("Rig driver")
                 .with_grants(api.VideoGrants(room_join=True, room=self.room_name))
                 .with_attributes({"tb.context": json.dumps(CONTEXT)})
                 .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=AGENT)])))

        @self.room.on("track_subscribed")
        def _track(track: rtc.Track, *_):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.ensure_future(self._listen(track))

        async def _text(reader, who):
            body = await reader.read_all()
            try:
                msg = json.loads(body)
            except ValueError:
                return
            if reader.info.topic == "tb.agent":
                self.replies.append(msg.get("reply", ""))
            else:
                self.events.append(msg)

        for topic in ("tb.agent", "tb.voice"):
            self.room.register_text_stream_handler(topic, lambda r, w: asyncio.ensure_future(_text(r, w)))
        await self.room.connect(os.environ["LIVEKIT_URL"], token.to_jwt())
        track = rtc.LocalAudioTrack.create_audio_track("mic", self.source)
        await self.room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        self._mic = asyncio.ensure_future(self._microphone())

    async def _microphone(self) -> None:
        """A steady 10 ms stream: queued clip audio when there is some, silence (or cab noise) otherwise."""
        pending = np.zeros(0, dtype=np.float32)
        noise_gain = None
        t_next = time.monotonic()
        while True:
            while len(pending) < FRAME and not self._queue.empty():
                pending = np.concatenate([pending, self._queue.get_nowait()])
            if len(pending) >= FRAME:
                chunk, pending = pending[:FRAME], pending[FRAME:]
            else:
                chunk = np.zeros(FRAME, dtype=np.float32)
            if self.snr is not None:
                if noise_gain is None:
                    speech_rms = np.sqrt(np.mean(clip("park") ** 2))
                    noise_gain = speech_rms / (10 ** (self.snr / 20))
                chunk = chunk + noise_gain * cab_noise(FRAME, self.rng)
            pcm = (np.clip(chunk, -1, 1) * 32767).astype(np.int16)
            await self.source.capture_frame(rtc.AudioFrame(pcm.tobytes(), SR, 1, FRAME))
            t_next += 0.01
            await asyncio.sleep(max(0.0, t_next - time.monotonic()))

    async def say(self, audio: np.ndarray) -> float:
        """Queue audio on the mic; returns when it has played out (the moment the driver stops talking)."""
        await self._queue.put(audio)
        await asyncio.sleep(len(audio) / SR + 0.02)
        return time.monotonic()

    async def _listen(self, track: rtc.Track) -> None:
        async for ev in rtc.AudioStream(track, sample_rate=SR, num_channels=1):
            x = np.frombuffer(ev.frame.data, dtype=np.int16).astype(np.float32) / 32768
            self.agent_frames.append(x)
            now = time.monotonic()
            loud = db(x) > SPEAKING_DB
            if loud:
                self.quiet_since = None
                if self.speaking_since is None:
                    self.speaking_since = now
                    self.talk_log.append((now, True))
            else:
                if self.quiet_since is None:
                    self.quiet_since = now
                # 500 ms of quiet ends a stretch of agent speech (gaps between words and sentences are shorter)
                if self.speaking_since is not None and now - self.quiet_since > 0.5:
                    self.speaking_since = None
                    self.talk_log.append((self.quiet_since, False))

    def started_after(self, t: float) -> Optional[float]:
        return next((ts for ts, on in self.talk_log if on and ts >= t), None)

    def stopped_after(self, t: float) -> Optional[float]:
        return next((ts for ts, on in self.talk_log if not on and ts >= t), None)

    async def wait_speaking(self, after: float, timeout: float = 20.0) -> Optional[float]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            s = self.started_after(after)
            if s:
                return s
            await asyncio.sleep(0.02)
        return None

    async def wait_quiet(self, after: float, timeout: float = 30.0) -> Optional[float]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            s = self.stopped_after(after)
            if s and self.speaking_since is None:
                return s
            await asyncio.sleep(0.02)
        return None

    def turns(self) -> int:
        return sum(e.get("kind") == "turn" for e in self.events)

    async def wait_turns(self, n: int, timeout: float = 40.0) -> bool:
        """The agent logs a `turn` event when a reply ends (spoken out or cut off): the reliable end of a reply."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.turns() >= n:
                return True
            await asyncio.sleep(0.05)
        return False

    def last_turn(self) -> dict:
        return next((e for e in reversed(self.events) if e.get("kind") == "turn"), {})

    async def close(self) -> None:
        if self._mic:
            self._mic.cancel()
        OUT.mkdir(exist_ok=True)
        if self.agent_frames:
            pcm = (np.clip(np.concatenate(self.agent_frames), -1, 1) * 32767).astype(np.int16)
            with wave.open(str(OUT / f"{self.room_name}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(pcm.tobytes())
        await self.room.disconnect()


async def _fresh(name: str, snr: Optional[float]) -> Driver:
    d = Driver(f"rig-{name}-{int(time.time())}", snr)
    await d.join()
    await asyncio.sleep(2.5)  # the agent joins and its session starts
    return d


async def complete(snr):
    d = await _fresh("complete", snr)
    try:
        end = await d.say(clip("park"))
        first = await d.wait_speaking(end)
        await d.wait_turns(1)
        return {"first_audio_ms": int((first - end) * 1000) if first else None, "turn": d.last_turn(),
                "reply": d.replies[-1:]}
    finally:
        await d.close()


async def pause(snr):
    d = await _fresh("pause", snr)
    try:
        t0 = time.monotonic()
        await d.say(np.concatenate([clip("find_a"), silence(1.2)]))
        early = d.started_after(t0) is not None
        end = await d.say(clip("showers"))
        first = await d.wait_speaking(end)
        await d.wait_turns(1)
        return {"early": early, "first_audio_ms": int((first - end) * 1000) if first else None,
                "turns": d.turns(), "turn": d.last_turn(), "reply": d.replies[-1:]}
    finally:
        await d.close()


async def barge(snr):
    d = await _fresh("barge", snr)
    try:
        end = await d.say(clip("park"))
        first = await d.wait_speaking(end)
        if not first:
            return {"error": "agent never spoke"}
        await asyncio.sleep(max(0.0, first + 1.0 - time.monotonic()))
        t_in = time.monotonic()
        asyncio.ensure_future(d.say(clip("reno")))
        stop = await d.wait_quiet(t_in, timeout=6)
        await d.wait_turns(2, timeout=45)
        return {"stop_ms": int((stop - t_in) * 1000) if stop else None,
                "turns": [e for e in d.events if e.get("kind") == "turn"], "replies": d.replies[-2:]}
    finally:
        await d.close()


async def backchannel(snr):
    d = await _fresh("backchannel", snr)
    try:
        end = await d.say(clip("park"))
        first = await d.wait_speaking(end)
        if not first:
            return {"error": "agent never spoke"}
        await asyncio.sleep(max(0.0, first + 1.0 - time.monotonic()))
        await d.say(clip("uhhuh"))
        await d.wait_turns(1)
        t = d.last_turn()
        return {"interrupted": t.get("interrupted"), "heard_chars": t.get("heard_chars"),
                "reply_chars": t.get("reply_chars"), "turns": d.turns(),
                "false_interruptions": sum(e.get("kind") == "false_interruption" for e in d.events)}
    finally:
        await d.close()


SCENARIOS = {"complete": complete, "pause": pause, "barge": barge, "backchannel": backchannel}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all", choices=["all", *SCENARIOS])
    ap.add_argument("--snr", type=float, default=None, help="cab noise under the driver, dB (none = clean)")
    a = ap.parse_args()
    names = list(SCENARIOS) if a.scenario == "all" else [a.scenario]
    for n in names:
        res = await SCENARIOS[n](a.snr)
        print(json.dumps({"scenario": n, "snr": a.snr, **res}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
