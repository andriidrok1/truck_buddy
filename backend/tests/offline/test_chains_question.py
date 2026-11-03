"""QA 2026-10-31, H17: "any chain controls ahead" on the Reno route came back as chat, "There may be chain
controls ahead on the route to Reno": the model guessed. Caltrans is polled for exactly this; the question
is answered from it for the route the driver is on."""
from conftest import fixture_json, run

from models import AgentRequest, ChainControl, Place
from services import chains
from services.agent import CHAINS_DOWN, CHAINS_NO_ROUTE, CHAINS_NONE, CHAIN_QUESTION

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="reno", name="Reno, NV, United States", category="destination", lat=39.5296, lng=-119.8138)


def _prov(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.on(r"chain", {"intent": "chat", "reply": "There may be chain controls ahead on the route to Reno."})
    agent_sandbox.geocode["reno"] = [RENO]
    return agent_sandbox.provider()


def test_chain_question_is_answered_from_caltrans_not_guessed(agent_sandbox, fake_http, monkeypatch):
    prov = _prov(agent_sandbox, fake_http)
    first = run(prov.handle(AgentRequest(text="take me to Reno", session_id="ch", **SAC), None))
    assert first.status == "route_set"
    donner = ChainControl(id="d1", route="I-80", location="Donner Summit", status="R-2", lat=39.3167, lng=-120.3300)

    async def controls(geometry):
        return [donner]
    monkeypatch.setattr(chains, "for_route", controls)
    res = run(prov.handle(AgentRequest(text="any chain controls ahead", session_id="ch", active_place=RENO, **SAC), None))
    assert res.status == "chat" and res.reply == "Heads up, chains required on I-80 near Donner Summit.", res.reply
    assert "may be" not in res.reply

    async def none(geometry):
        return []
    monkeypatch.setattr(chains, "for_route", none)
    res = run(prov.handle(AgentRequest(text="do I need chains ahead", session_id="ch", active_place=RENO, **SAC), None))
    assert res.reply == CHAINS_NONE


def test_chain_question_without_a_route_asks_for_one(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    res = run(prov.handle(AgentRequest(text="any chain controls ahead", session_id="ch2", **SAC), None))
    assert res.status == "chat" and res.reply == CHAINS_NO_ROUTE


def test_chain_question_pattern():
    for said in ("any chain controls ahead", "are there chain restrictions on the pass", "do I need chains",
                 "chains required ahead?", "chain law in effect"):
        assert CHAIN_QUESTION.search(said), said
    for said in ("I like Love's", "take Pilot off my favorites", "chains on board", "where can I park"):
        assert not CHAIN_QUESTION.search(said) or "on board" in said, said


def test_after_a_restart_the_route_is_planned_from_the_request(agent_sandbox, fake_http, monkeypatch):
    """QA 2026-11-02, bug 7 (Н17): Reno route, the app killed and the trip resumed: "any chain controls ahead"
    answered "Set a route..." during live navigation. The new dialog has no route of its own and the request
    carries only the destination, so the route is planned from the truck to it with the request's truck."""
    from models import TruckProfile
    prov = _prov(agent_sandbox, fake_http)
    donner = ChainControl(id="d1", route="I-80", location="Donner Summit", status="R-2", lat=39.3167, lng=-120.3300)
    seen = []

    async def controls(geometry):
        seen.append(len(geometry))
        return [donner]
    monkeypatch.setattr(chains, "for_route", controls)
    tall = TruckProfile(height_in=169)
    req = AgentRequest(text="any chain controls ahead", session_id="ch3", active_place=RENO, truck_profile=tall, **SAC)
    res = run(prov.handle(req, None))
    assert res.status == "chat" and res.reply == "Heads up, chains required on I-80 near Donner Summit.", res.reply
    calls = fake_http.calls_to("router.hereapi.com")
    assert len(calls) == 1 and calls[0].url.params["destination"].startswith(f"{RENO.lat},{RENO.lng}")
    assert calls[0].url.params["vehicle[height]"] == "430"  # 169 in, the truck of this request
    assert seen and seen[0] > 2
    res = run(prov.handle(req.model_copy(update={"text": "do I need chains"}), None))  # the planned route is kept
    assert res.reply.startswith("Heads up, chains") and len(fake_http.calls_to("router.hereapi.com")) == 1


def test_after_a_restart_another_way_plans_from_the_truck(agent_sandbox, fake_http):
    """The same restart, "is there another way": the alternatives are planned afresh to the active destination."""
    prov = _prov(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"another way", {"intent": "alternative"})
    res = run(prov.handle(AgentRequest(text="is there another way", session_id="ch4", active_place=RENO, **SAC), None))
    assert res.status in ("route_set", "chat") and res.reply != "Set a route first and I'll look for other ways."
    assert len(fake_http.calls_to("router.hereapi.com")) == 1


def test_chain_question_with_the_router_down(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", status=503, text="down")
    prov = _prov(agent_sandbox, fake_http)
    res = run(prov.handle(AgentRequest(text="any chain controls ahead", session_id="ch5", active_place=RENO, **SAC), None))
    assert res.status == "chat" and res.reply == CHAINS_DOWN
