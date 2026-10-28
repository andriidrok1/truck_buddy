"""Long-term memory never holds the first reply: a slow read is picked up by the next turn."""
import asyncio
import time

from conftest import run

from models import AgentRequest
from services import agent, memory

SAC = {"lat": 38.5816, "lng": -121.4944}
PAST = [{"role": "driver", "text": "I like Pilot", "at": "2026-10-01T00:00:00+00:00"},
        {"role": "copilot", "text": "Noted.", "at": "2026-10-01T00:00:01+00:00"}]


def _chat(agent_sandbox):
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure."}
    return agent_sandbox.provider()


def test_slow_memory_does_not_delay_the_first_turn(agent_sandbox, monkeypatch):
    async def slow(driver, limit):
        await asyncio.sleep(1.0)
        return PAST

    monkeypatch.setattr(memory, "recent", slow)
    monkeypatch.setattr(agent, "MEMORY_FIRST_TURN_S", 0.05)
    prov = _chat(agent_sandbox)

    async def flow():
        t0 = time.perf_counter()
        first = await prov.handle(AgentRequest(text="hi", session_id="m1", **SAC), "drv")
        took = time.perf_counter() - t0
        ctx_first = agent_sandbox.llm.calls[-1][1]
        await asyncio.sleep(1.1)  # the read finishes in the background
        await prov.handle(AgentRequest(text="hello again", session_id="m1", **SAC), "drv")
        ctx_second = agent_sandbox.llm.calls[-1][1]
        return first, took, ctx_first, ctx_second

    first, took, ctx_first, ctx_second = run(flow())
    assert first.status == "chat" and took < 0.5
    assert "I like Pilot" not in ctx_first
    assert "I like Pilot" in ctx_second and "Noted." not in ctx_second  # only the driver's lines
    assert "past_task" not in agent._SESSIONS["m1"]


def test_fast_memory_is_in_the_first_turn(agent_sandbox, monkeypatch):
    async def fast(driver, limit):
        return PAST

    monkeypatch.setattr(memory, "recent", fast)
    prov = _chat(agent_sandbox)
    run(prov.handle(AgentRequest(text="hi", session_id="m2", **SAC), "drv"))
    assert "I like Pilot" in agent_sandbox.llm.calls[-1][1]


def test_memory_errors_and_timeouts_are_silent(agent_sandbox, monkeypatch):
    async def boom(driver, limit):
        raise RuntimeError("db down")

    monkeypatch.setattr(memory, "recent", boom)
    prov = _chat(agent_sandbox)
    res = run(prov.handle(AgentRequest(text="hi", session_id="m3", **SAC), "drv"))
    assert res.status == "chat" and agent._SESSIONS["m3"].get("past", []) == []

    async def forever(driver, limit):
        await asyncio.sleep(10)
        return PAST

    monkeypatch.setattr(memory, "recent", forever)
    monkeypatch.setattr(agent, "MEMORY_FIRST_TURN_S", 0.01)
    monkeypatch.setattr(agent, "MEMORY_LOAD_S", 0.05)

    async def flow():
        await prov.handle(AgentRequest(text="hi", session_id="m4", **SAC), "drv")
        await asyncio.sleep(0.2)
        await prov.handle(AgentRequest(text="again", session_id="m4", **SAC), "drv")
        return agent._SESSIONS["m4"]

    sess = run(flow())
    assert sess.get("past", []) == [] and "past_task" not in sess


def test_no_driver_means_no_memory_read(agent_sandbox, monkeypatch):
    calls = []

    async def spy(driver, limit):
        calls.append(driver)
        return PAST

    monkeypatch.setattr(memory, "recent", spy)
    prov = _chat(agent_sandbox)
    run(prov.handle(AgentRequest(text="hi", session_id="anon", **SAC), None))
    assert calls == []
