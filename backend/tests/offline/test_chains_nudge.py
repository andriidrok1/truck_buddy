"""The chain-control question: asked once when chains are required ahead, answered with yes/no, no -> a stop."""
from conftest import fixture_json, run

from models import AgentRequest, ChainAhead, NudgeRequest, Place, TruckProfile
from services import agent, controls, nudges

SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
AHEAD = [[38.60 + i * 0.04, -121.45 + i * 0.09, 5.0 * i] for i in range(1, 13)]
PILOT = Place(id="g-pilot", name="Pilot Travel Center, Auburn", category="truck_stop", lat=AHEAD[4][0] + 0.005,
              lng=AHEAD[4][1])  # ~25 miles ahead
KINGVALE = ChainAhead(route="I-80", location="Kingvale", status="R-2", miles_ahead=45)


def _req(**kw):
    base = dict(driving=True, active_place=RENO, ahead=AHEAD, session_id="ch", chain_control_ahead=KINGVALE, **SAC)
    base.update(kw)
    return NudgeRequest(**base)


def test_profile_and_control():
    assert "carries tire chains" in TruckProfile(chains_on_board=True).prefs_label
    assert "no tire chains on board" in TruckProfile(chains_on_board=False).prefs_label
    assert "chains" not in TruckProfile().prefs_label
    req = AgentRequest(text="x", **SAC)
    acts, say, _ = controls.apply_actions([{"type": "set_chains_on_board", "value": "yes"}], req)
    assert acts[0].type == "set_chains_on_board" and acts[0].value is True and "chains on board" in say
    acts, say, _ = controls.apply_actions([{"type": "set_nudges", "value": False}], req)
    assert acts[0].value is False and "stop suggesting" in say


def test_asks_once_and_yes_records_chains(agent_sandbox):
    agent_sandbox.places_provider.places = [PILOT]
    sess = agent._session("ch")
    out = run(nudges.compute(_req(), sess, None))
    assert len(out) == 1 and out[0].kind == "chains" and out[0].offer == "chains_answer"
    assert out[0].text == "Chains required ahead on I-80 near Kingvale. Do you have chains on board?"
    assert out[0].place.id == PILOT.id
    assert run(nudges.compute(_req(), sess, None)) == []  # once
    req = AgentRequest(text="yes", session_id="ch", active_place=RENO, **SAC)
    res = run(agent_sandbox.provider().handle(req, None))
    assert res.status == "chat" and res.reply == "Got it, chains on board."
    assert [a.type for a in res.actions] == ["set_chains_on_board"] and res.actions[0].value is True
    assert len(agent_sandbox.llm.calls) == 0


def test_no_records_none_and_offers_the_last_stop(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.add("cwwp2.dot.ca.gov", "/data/", json={"data": []})
    agent_sandbox.places_provider.places = [PILOT]
    sess = agent._session("ch2")
    run(nudges.compute(_req(session_id="ch2"), sess, None))
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="no", session_id="ch2", active_place=RENO, **SAC), None))
    assert res.reply == ("Got it, no chains on board. The last truck stop before the control is Pilot Travel Center, "
                         "25 miles ahead, want me to add it?")
    assert res.actions[0].type == "set_chains_on_board" and res.actions[0].value is False
    res = run(prov.handle(AgentRequest(text="yes", session_id="ch2", active_place=RENO, **SAC), None))
    assert res.status == "route_set" and res.reply.startswith("Added Pilot Travel Center.")


def test_known_no_chains_offers_directly_and_known_chains_stays_quiet(agent_sandbox):
    agent_sandbox.places_provider.places = [PILOT]
    out = run(nudges.compute(_req(session_id="ch3", truck_profile=TruckProfile(chains_on_board=False)),
                             agent._session("ch3"), None))
    assert out and out[0].text.startswith("Chains required ahead on I-80 near Kingvale, and you've got no chains on "
                                          "board. The last truck stop before it is Pilot Travel Center, 25 miles")
    assert out[0].offer == "add_stop"
    assert run(nudges.compute(_req(session_id="ch4", truck_profile=TruckProfile(chains_on_board=True)),
                              agent._session("ch4"), None)) == []


def test_severity_distance_and_flag(agent_sandbox, monkeypatch):
    agent_sandbox.places_provider.places = [PILOT]
    r1 = ChainAhead(route="I-80", location="Kingvale", status="R-1", miles_ahead=45)
    assert run(nudges.compute(_req(session_id="s1", chain_control_ahead=r1,
                                   truck_profile=TruckProfile(trailer_ft=28)), agent._session("s1"), None)) == []
    assert run(nudges.compute(_req(session_id="s2", chain_control_ahead=r1,
                                   truck_profile=TruckProfile(trailer_ft=53)), agent._session("s2"), None))
    far = ChainAhead(route="I-80", location="Kingvale", status="R-3", miles_ahead=120)
    assert run(nudges.compute(_req(session_id="s3", chain_control_ahead=far), agent._session("s3"), None)) == []
    assert run(nudges.compute(_req(session_id="s4", chain_control_ahead=None), agent._session("s4"), None)) == []
    monkeypatch.setenv("NUDGE_CHAINS", "0")
    assert run(nudges.compute(_req(session_id="s5"), agent._session("s5"), None)) == []


def test_no_stop_before_the_control_still_answers(agent_sandbox):
    agent_sandbox.places_provider.places = []
    sess = agent._session("ch5")
    out = run(nudges.compute(_req(session_id="ch5"), sess, None))
    assert out and out[0].place is None
    res = run(agent_sandbox.provider().handle(AgentRequest(text="nope", session_id="ch5", active_place=RENO, **SAC),
                                              None))
    assert res.reply == nudges.CHAINS_NO_STOP and "pending" not in sess
