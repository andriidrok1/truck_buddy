"""QA 2026-11-02 (UX): "I have a flat tire" -> "Which tire, steer, drive or trailer? ..." -> "call the cheapest" ->
the same question again, word for word. The answer was not an answer, the dialog dropped the question, and the
model read the breakdown again, which asked again. The same question is never asked twice in a row: the
second time the search starts with the sensible default (someone who comes out) and the copilot says so."""
import re

from conftest import run

from models import AgentRequest, AgentResponse
from services import agent, breakdown

SAC = {"lat": 38.5816, "lng": -121.4944}
REAL_HANDLE = breakdown.handle  # the sandbox switches the breakdown turn off; this test needs it
FLAT = {"intent": "breakdown", "kind": "tire", "detail": "flat tire", "mode": "unknown", "tire_position": None}


def _prov(agent_sandbox, monkeypatch):
    monkeypatch.setattr(agent.breakdown, "handle", REAL_HANDLE)
    started = []

    def start(d, req, sess, text, source):
        started.append(dict(d))
        return AgentResponse(status="researching", transcript=text, reply=breakdown.START, research_id="r1",
                             source=source)

    monkeypatch.setattr(breakdown, "start_research", start)
    llm = agent_sandbox.llm
    llm.rules.insert(0, (re.compile(r"Then answered: call the cheapest", re.I), {"breakdown": False, "changed_topic": True}))
    llm.on(r"flat tire", FLAT)
    llm.on(r"^call the cheapest", FLAT)  # what the model made of it, with the breakdown in the history
    return agent_sandbox.provider(), started


def test_the_question_is_not_asked_twice_in_a_row(agent_sandbox, monkeypatch):
    prov, started = _prov(agent_sandbox, monkeypatch)
    first = run(prov.handle(AgentRequest(text="I have a flat tire", session_id="bd1", **SAC), None))
    assert first.reply == breakdown.QUESTIONS["tire"] and not started
    again = run(prov.handle(AgentRequest(text="call the cheapest", session_id="bd1", **SAC), None))
    assert again.reply != breakdown.QUESTIONS["tire"]
    assert again.status == "researching" and started and started[0]["kind"] == "tire"
    assert again.reply == ("Okay, I'll look for someone who can come out to you, tell them which tire when you call. "
                           "Give me a minute, I'll check who's around and what drivers say about them.")


def test_a_new_breakdown_later_is_asked_again(agent_sandbox, monkeypatch):
    prov, started = _prov(agent_sandbox, monkeypatch)
    agent_sandbox.llm.on(r"^thanks", {"intent": "chat", "reply": "Anytime."})
    run(prov.handle(AgentRequest(text="I have a flat tire", session_id="bd2", **SAC), None))
    run(prov.handle(AgentRequest(text="call the cheapest", session_id="bd2", **SAC), None))
    run(prov.handle(AgentRequest(text="thanks", session_id="bd2", **SAC), None))
    res = run(prov.handle(AgentRequest(text="I have a flat tire", session_id="bd2", **SAC), None))
    assert res.reply == breakdown.QUESTIONS["tire"]
