"""Reply length follows the truck's speed: one sentence on the highway, up to three parked, templates untouched."""
from conftest import run

from models import AgentRequest, AgentResponse
from services import agent
from services.speech_text import fit_reply

SAC = {"lat": 38.5816, "lng": -121.4944}
THREE = "Rain starts around Auburn. Expect slow traffic past Colfax. Chains are not required yet."


def test_fit_reply():
    assert fit_reply(THREE, 1) == "Rain starts around Auburn."
    assert fit_reply(THREE, 2) == "Rain starts around Auburn. Expect slow traffic past Colfax."
    assert fit_reply(THREE, 5) == THREE and fit_reply(THREE, 0) == THREE
    assert fit_reply("Take exit 12 on U.S. 50. Then left.", 1) == "Take exit 12 on U.S. 50."  # abbreviations keep
    assert fit_reply("", 1) == "" and fit_reply("  one   line ", 1) == "one line"


def _res(status="chat"):
    return AgentResponse(status=status, transcript="x", reply=THREE, source="test")


def test_fit_for_speed(monkeypatch):
    fast, slow, unknown = (AgentRequest(text="x", speed_mps=v, **SAC) for v in (30.0, 0.0, None))
    r = _res()
    agent._fit_for_speed(fast, r)
    assert r.reply == "Rain starts around Auburn."
    r = _res()
    agent._fit_for_speed(slow, r)
    assert r.reply == THREE  # three sentences fit when parked
    r = _res()
    r.reply = THREE + " And one more thing to say here."
    agent._fit_for_speed(slow, r)
    assert r.reply == THREE
    r = _res()
    agent._fit_for_speed(unknown, r)
    assert r.reply == THREE  # no speed, no cut
    for status in ("route_set", "confirm", "options", "providers"):
        r = _res(status)
        agent._fit_for_speed(fast, r)
        assert r.reply == THREE
    monkeypatch.setenv("REPLY_FAST_MPS", "40")
    r = _res()
    agent._fit_for_speed(fast, r)
    assert r.reply == THREE  # 30 m/s is below the raised threshold
    monkeypatch.setenv("REPLY_FIT", "0")
    r = _res()
    agent._fit_for_speed(AgentRequest(text="x", speed_mps=50.0, **SAC), r)
    assert r.reply == THREE


def test_speed_in_context_and_on_a_chat_turn(agent_sandbox):
    prov = agent_sandbox.provider()
    ctx = prov._context(AgentRequest(text="hi", speed_mps=30.0, **SAC), {"history": [], "places": []})
    assert "highway speed: answer in one short sentence" in ctx
    ctx = prov._context(AgentRequest(text="hi", speed_mps=5.0, **SAC), {"history": [], "places": []})
    assert "highway speed" not in ctx
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure thing. Rain near Auburn."}
    res = run(prov.handle(AgentRequest(text="how's the weather", session_id="sp", speed_mps=30.0, **SAC), None))
    assert res.reply == "Sure thing."
    res = run(prov.handle(AgentRequest(text="how's the weather", session_id="sp", speed_mps=0.0, **SAC), None))
    assert res.reply == "Sure thing. Rain near Auburn."


def test_speed_field_bounds():
    assert AgentRequest(text="x", speed_mps=60, **SAC).speed_mps == 60
    for bad in (-1, 61):
        try:
            AgentRequest(text="x", speed_mps=bad, **SAC)
            assert False, bad
        except ValueError:
            pass
